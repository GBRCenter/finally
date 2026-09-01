"""Tests for the SSE price stream and the price history endpoint.

The SSE generator is driven directly with a fake Request object rather than
through a live server (per the design doc) — that makes the keepalive path
testable with a monkeypatched threshold instead of 15 seconds of real
waiting, and needs no ASGI test client.
"""

from __future__ import annotations

import json

import pytest

import app.market.stream as stream_module
from app.market.cache import HISTORY_MAXLEN, PriceCache
from app.market.stream import _generate_events, create_history_router


class FakeClient:
    host = "test-client"


class FakeRequest:
    """Minimal stand-in for a Starlette Request, as far as the generator cares."""

    def __init__(self, disconnect_after: int | None = None) -> None:
        self.client = FakeClient()
        self._checks = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        if self._disconnect_after is not None and self._checks > self._disconnect_after:
            return True
        return False


def _history_endpoint(cache: PriceCache):
    """Grab the just-registered endpoint closure without a live ASGI app."""
    router = create_history_router(cache)
    return router.routes[-1].endpoint


@pytest.mark.asyncio
class TestGenerateEvents:
    async def test_first_event_is_the_retry_directive(self):
        cache = PriceCache()
        agen = _generate_events(cache, FakeRequest(), interval=0.01)
        assert await agen.__anext__() == "retry: 1000\n\n"
        await agen.aclose()

    async def test_connecting_client_gets_a_full_snapshot_immediately(self):
        """A fresh generator starts at last_version = -1, so the very first
        comparison always differs -- this is also what makes reconnects work,
        with no separate snapshot endpoint needed."""
        cache = PriceCache()
        cache.update("AAPL", 190.52)
        cache.update("GOOGL", 175.00)

        agen = _generate_events(cache, FakeRequest(), interval=0.01)
        await agen.__anext__()  # retry directive
        event = await agen.__anext__()
        await agen.aclose()

        assert event.startswith("data: ")
        payload = json.loads(event[len("data: ") : -2])
        assert set(payload.keys()) == {"AAPL", "GOOGL"}

    async def test_payload_is_map_keyed_by_ticker_with_frozen_field_shape(self):
        cache = PriceCache()
        cache.update("AAPL", 190.52, timestamp=1755873791.482)

        agen = _generate_events(cache, FakeRequest(), interval=0.01)
        await agen.__anext__()
        event = await agen.__anext__()
        await agen.aclose()

        payload = json.loads(event[len("data: ") : -2])
        aapl = payload["AAPL"]
        assert aapl["ticker"] == "AAPL"
        assert aapl["timestamp"] == 1755873791.482  # float seconds, never ISO
        assert set(aapl.keys()) == {
            "ticker",
            "price",
            "previous_price",
            "timestamp",
            "change",
            "change_percent",
            "direction",
        }

    async def test_keepalive_ping_sent_when_idle_not_a_duplicate_data_event(self, monkeypatch):
        """While the version is unchanged, the next event must be a keepalive
        ping — never a repeated data event — and it must wait for the
        threshold rather than firing immediately."""
        monkeypatch.setattr(stream_module, "KEEPALIVE_SECONDS", 0.03)
        cache = PriceCache()
        cache.update("AAPL", 100.0)

        agen = stream_module._generate_events(cache, FakeRequest(), interval=0.01)
        await agen.__anext__()  # retry
        await agen.__anext__()  # initial snapshot
        ping = await agen.__anext__()
        await agen.aclose()

        assert ping == ": ping\n\n"

    async def test_new_price_after_a_ping_produces_a_fresh_data_event(self, monkeypatch):
        monkeypatch.setattr(stream_module, "KEEPALIVE_SECONDS", 0.03)
        cache = PriceCache()
        cache.update("AAPL", 100.0)

        agen = stream_module._generate_events(cache, FakeRequest(), interval=0.01)
        await agen.__anext__()  # retry
        await agen.__anext__()  # initial snapshot
        await agen.__anext__()  # ping

        cache.update("AAPL", 101.0)
        event = await agen.__anext__()
        await agen.aclose()

        assert event.startswith("data: ")
        payload = json.loads(event[len("data: ") : -2])
        assert payload["AAPL"]["price"] == 101.0

    async def test_generator_stops_when_client_disconnects(self):
        cache = PriceCache()
        request = FakeRequest(disconnect_after=0)

        agen = _generate_events(cache, request, interval=0.01)
        await agen.__anext__()  # retry directive
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

    async def test_empty_cache_sends_no_data_event(self):
        # Let the loop run one full iteration (version differs from the
        # initial -1, but there are no prices to send) before disconnecting,
        # so the assertion is actually exercising the empty-prices branch.
        cache = PriceCache()
        request = FakeRequest(disconnect_after=1)

        agen = _generate_events(cache, request, interval=0.01)
        events = [event async for event in agen]

        assert events == ["retry: 1000\n\n"]


@pytest.mark.asyncio
class TestPriceHistoryEndpoint:
    async def test_returns_points_oldest_first(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1.0)
        cache.update("AAPL", 191.00, timestamp=2.0)

        result = await _history_endpoint(cache)(ticker="AAPL", limit=HISTORY_MAXLEN)

        assert result["ticker"] == "AAPL"
        assert result["points"] == [
            {"timestamp": 1.0, "price": 190.00},
            {"timestamp": 2.0, "price": 191.00},
        ]

    async def test_untracked_ticker_returns_empty_points_not_an_error(self):
        cache = PriceCache()

        result = await _history_endpoint(cache)(ticker="NOPE", limit=HISTORY_MAXLEN)

        assert result == {"ticker": "NOPE", "points": []}

    async def test_ticker_is_normalized(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1.0)

        result = await _history_endpoint(cache)(ticker=" aapl ", limit=HISTORY_MAXLEN)

        assert result["ticker"] == "AAPL"
        assert len(result["points"]) == 1

    async def test_limit_is_clamped_to_history_maxlen(self):
        cache = PriceCache()
        for i in range(5):
            cache.update("AAPL", 100.0 + i, timestamp=float(i))

        result = await _history_endpoint(cache)(ticker="AAPL", limit=1_000_000)

        assert len(result["points"]) == 5

    async def test_limit_narrower_than_history_returns_most_recent(self):
        cache = PriceCache()
        for i in range(5):
            cache.update("AAPL", 100.0 + i, timestamp=float(i))

        result = await _history_endpoint(cache)(ticker="AAPL", limit=2)

        assert [p["timestamp"] for p in result["points"]] == [3.0, 4.0]
