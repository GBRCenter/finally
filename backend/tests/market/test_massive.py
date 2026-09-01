"""Tests for MassiveDataSource.

Snapshot parsing is tested against the real `TickerSnapshot` model
(`TickerSnapshot.from_dict`), not `MagicMock`. A mock answers to any
attribute name you give it, including a misspelled one — which is exactly
how the shipped code's `last_trade.timestamp` bug (the real attribute is
`sip_timestamp`) went undetected at 94% coverage. Only network calls
(`_fetch_snapshots`) are mocked; the parsing path always exercises the real
model shape.
"""

from unittest.mock import MagicMock, patch

import pytest
from massive.exceptions import AuthError, BadResponse
from massive.rest.models.snapshot import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import NANOS_PER_SECOND, MassiveDataSource


def _make_snapshot(ticker: str, price: float, timestamp_ns: int) -> TickerSnapshot:
    """Build a real TickerSnapshot from a Massive-shaped payload (wire keys)."""
    return TickerSnapshot.from_dict(
        {
            "ticker": ticker,
            "lastTrade": {"p": price, "s": 100, "t": timestamp_ns, "x": 4},
        }
    )


def _make_snapshot_without_trade(ticker: str) -> TickerSnapshot:
    return TickerSnapshot.from_dict({"ticker": ticker})


@pytest.mark.asyncio
class TestApplySnapshots:
    """Tests for the extracted, directly-testable parse method."""

    def test_snapshot_parse_produces_a_present_day_timestamp(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        snap = _make_snapshot("AAPL", 190.52, 1755873791482000000)

        processed = source._apply_snapshots([snap])

        assert processed == 1
        update = cache.get("AAPL")
        assert update is not None
        assert update.price == 190.52
        # Plausible present, in SECONDS -- not ~1.76e15 (the pre-fix bug).
        assert 1_600_000_000 < update.timestamp < 2_000_000_000

    def test_snapshot_timestamp_matches_expected_conversion(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        timestamp_ns = 1707580800000000000
        snap = _make_snapshot("AAPL", 190.50, timestamp_ns)

        source._apply_snapshots([snap])

        update = cache.get("AAPL")
        assert update.timestamp == timestamp_ns / NANOS_PER_SECOND

    def test_snapshot_without_a_last_trade_is_skipped(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        snap = _make_snapshot_without_trade("AAPL")

        processed = source._apply_snapshots([snap])

        assert processed == 0
        assert cache.get("AAPL") is None

    def test_mixed_snapshots_processes_only_the_valid_one(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        good = _make_snapshot("AAPL", 190.50, 1707580800000000000)
        bad = _make_snapshot_without_trade("MISSING")

        processed = source._apply_snapshots([good, bad])

        assert processed == 1
        assert cache.get_price("AAPL") == 190.50
        assert cache.get("MISSING") is None

    def test_multiple_tickers_all_update(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        snapshots = [
            _make_snapshot("AAPL", 190.50, 1707580800000000000),
            _make_snapshot("GOOGL", 175.25, 1707580800000000000),
        ]

        processed = source._apply_snapshots(snapshots)

        assert processed == 2
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25


@pytest.mark.asyncio
class TestMassiveDataSourcePolling:
    """Unit tests for the async polling lifecycle, with the network mocked."""

    async def test_poll_updates_cache(self):
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,  # Long interval so the loop doesn't auto-poll
        )
        source._tickers = ["AAPL", "GOOGL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [
            _make_snapshot("AAPL", 190.50, 1707580800000000000),
            _make_snapshot("GOOGL", 175.25, 1707580800000000000),
        ]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_bad_response_leaves_cache_untouched_and_does_not_raise(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(
            source,
            "_fetch_snapshots",
            side_effect=BadResponse("rate limited"),
        ):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") is None

    async def test_auth_error_propagates(self):
        """An unrecoverable auth failure should not be retried silently."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="bad-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", side_effect=AuthError("invalid key")):
            with pytest.raises(AuthError):
                await source._poll_once()

    async def test_unexpected_error_does_not_crash(self):
        """Test that unexpected (e.g. network) errors don't crash the poller."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") is None  # No update happened

    async def test_add_ticker(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("AAPL")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_uppercase_normalization(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("aapl")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_strips_whitespace(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("  AAPL  ")
        assert "AAPL" in source.get_tickers()

    async def test_remove_ticker(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker("AAPL")
        assert "AAPL" not in source.get_tickers()
        assert cache.get("AAPL") is None

    async def test_get_tickers(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]

        tickers = source.get_tickers()
        assert tickers == ["AAPL", "GOOGL"]

    async def test_empty_tickers_skips_poll(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = []

        # Should not call _fetch_snapshots
        with patch.object(source, "_fetch_snapshots") as mock_fetch:
            await source._poll_once()
            mock_fetch.assert_not_called()

    async def test_stop_is_idempotent(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.stop()
        await source.stop()  # Should not raise

    async def test_stop_cancels_task(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=10.0)

        # Mock the client and start
        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        # Verify task is running
        assert source._task is not None
        assert not source._task.done()

        # Stop and verify task is cancelled
        await source.stop()
        assert source._task is None

    async def test_start_immediate_poll(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1707580800000000000)]

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
                await source.start(["AAPL"])

        # Cache should have data immediately from the first poll
        assert cache.get_price("AAPL") == 190.50

        await source.stop()
