"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import HISTORY_MAXLEN, PriceCache

logger = logging.getLogger(__name__)

# How long the cache version can go unchanged before we send an SSE comment
# line to keep the connection (and proxies in between) from timing it out.
KEEPALIVE_SECONDS = 15.0


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create the SSE streaming router with a reference to the price cache.

    This factory pattern lets us inject the PriceCache without globals. A
    fresh APIRouter is built on every call so that constructing the app more
    than once per process (a common pytest fixture pattern) never appends
    duplicate routes to shared module state.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        Streams all tracked ticker prices every ~500ms. The client connects
        with EventSource and receives events in the format:

            data: {"AAPL": {"ticker": "AAPL", "price": 190.50, ...}, ...}

        Includes a retry directive so the browser auto-reconnects on
        disconnection (EventSource built-in behavior).
        """
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
            },
        )

    return router


def create_history_router(price_cache: PriceCache) -> APIRouter:
    """Create the router serving rolling in-memory price history.

    Factory pattern mirrors create_stream_router: a fresh APIRouter is built
    on every call so the PriceCache is injected without module-level globals
    that would accumulate duplicate routes across repeated app construction.
    """
    history_router = APIRouter(prefix="/api/prices", tags=["prices"])

    @history_router.get("/{ticker}/history")
    async def get_price_history(ticker: str, limit: int = HISTORY_MAXLEN) -> dict:
        """Rolling in-memory price history for the main chart.

        Returns an empty `points` list for an untracked ticker — not a 404 —
        so the chart draws nothing rather than erroring. Oldest-first,
        matching what a left-to-right time axis wants.
        """
        ticker = ticker.strip().upper()
        limit = max(1, min(limit, HISTORY_MAXLEN))
        points = price_cache.get_history(ticker, limit=limit)
        return {
            "ticker": ticker,
            "points": [{"timestamp": ts, "price": price} for ts, price in points],
        }

    return history_router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = 0.5,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted price events.

    Sends all prices every `interval` seconds whenever the cache version has
    changed. When the version is unchanged for KEEPALIVE_SECONDS, sends an
    SSE comment line instead — EventSource ignores it, but it keeps the
    connection (and any proxy in between) from treating a quiet market as a
    dead connection. Stops when the client disconnects.
    """
    # Tell the client to retry after 1 second if the connection drops
    yield "retry: 1000\n\n"

    last_version = -1
    last_sent = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            current_version = price_cache.version
            if current_version != last_version:
                last_version = current_version
                prices = price_cache.get_all()

                if prices:
                    data = {ticker: update.to_dict() for ticker, update in prices.items()}
                    payload = json.dumps(data)
                    yield f"data: {payload}\n\n"
                    last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= KEEPALIVE_SECONDS:
                yield ": ping\n\n"
                last_sent = time.monotonic()

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
