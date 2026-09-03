"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time

from massive import RESTClient
from massive.exceptions import AuthError, BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

# Snapshot last_trade.sip_timestamp is Unix nanoseconds; PriceCache wants seconds.
NANOS_PER_SECOND = 1_000_000_000


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all watched
    tickers in a single API call, then writes results to the PriceCache.

    Rate limits:
      - Free tier: 5 req/min → poll every 15s (default)
      - Paid tiers: higher limits → poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        # Flipped to False if the poll loop dies (e.g. a revoked API key
        # raising AuthError after start() already succeeded). Nothing awaits
        # self._task until stop(), so without this the failure would only
        # ever surface as an unretrieved-exception log at GC time. A future
        # GET /api/health can report `market_source` as degraded by reading
        # this flag.
        self._healthy = True

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)

        # Do an immediate first poll so the cache has data right away
        await self._poll_once()

        self._healthy = True
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        self._task.add_done_callback(self._on_poll_task_done)
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    @property
    def is_healthy(self) -> bool:
        """False once the poll loop has died from an unhandled exception.

        A deliberate stop() (which cancels the task) never flips this.
        """
        return self._healthy

    # --- Internal ---

    def _on_poll_task_done(self, task: asyncio.Task) -> None:
        """Surface a dead poll loop loudly instead of an easy-to-miss
        "Task exception was never retrieved" log at garbage-collection time.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._healthy = False
            logger.critical(
                "Massive poller task died unexpectedly, live prices are now frozen: %s",
                exc,
                exc_info=exc,
            )

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch snapshots, update cache."""
        if not self._tickers or not self._client:
            return

        try:
            # The Massive RESTClient is synchronous — run in a thread to
            # avoid blocking the event loop.
            snapshots = await asyncio.to_thread(self._fetch_snapshots)
        except AuthError:
            logger.error("Massive API key rejected — the source does not fall back automatically")
            raise  # unrecoverable: do not retry on a loop
        except BadResponse as e:
            logger.warning("Massive returned an error response: %s", e)
            return  # transient: retry next interval
        except Exception:
            logger.exception("Massive poll failed")
            return

        processed = self._apply_snapshots(snapshots)
        logger.debug("Massive poll: updated %d/%d tickers", processed, len(self._tickers))

    def _apply_snapshots(self, snapshots: list) -> int:
        """Write snapshot data into the cache. Returns the number of tickers updated.

        Extracted from _poll_once so it can be tested directly against real
        `TickerSnapshot` objects (built via `TickerSnapshot.from_dict(...)`)
        instead of mocks that would silently accept a misspelled attribute.
        """
        processed = 0
        for snap in snapshots:
            trade = snap.last_trade
            if trade is None or trade.price is None:
                # No trade yet today (pre-market, or an unrecognized symbol
                # that still made it into the response) — leave it as "—".
                continue
            self._cache.update(
                ticker=snap.ticker,
                price=trade.price,
                timestamp=(
                    trade.sip_timestamp / NANOS_PER_SECOND if trade.sip_timestamp else time.time()
                ),
            )
            processed += 1
        return processed

    def _fetch_snapshots(self) -> list:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )
