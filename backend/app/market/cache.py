"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

from .models import PriceUpdate

# ~5 minutes of history at the 500ms simulator cadence. Under Massive's 15s
# poll interval this fills much more slowly (one point per poll), which is
# correct: the chart is sparse but accurate rather than padded with guesses.
HISTORY_MAXLEN = 600


class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution,
    the price history endpoint.
    """

    def __init__(self, history_maxlen: int = HISTORY_MAXLEN) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._history_maxlen = history_maxlen
        self._lock = Lock()
        self._version: int = 0  # Monotonically increasing; bumped on every update

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        """Record a new price for a ticker. Returns the created PriceUpdate.

        Automatically computes direction and change from the previous price.
        If this is the first update for the ticker, previous_price == price (direction='flat').
        Also appends to the ticker's rolling history (see get_history()).
        """
        with self._lock:
            ts = timestamp if timestamp is not None else time.time()
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
            )
            self._prices[ticker] = update

            history = self._history.get(ticker)
            if history is None:
                history = deque(maxlen=self._history_maxlen)
                self._history[ticker] = history
            history.append((ts, update.price))

            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Get the latest price for a single ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Snapshot of all current prices. Returns a shallow copy."""
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: get just the price float, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache (e.g., when removed from watchlist)."""
        with self._lock:
            self._prices.pop(ticker, None)
            self._history.pop(ticker, None)

    def get_history(self, ticker: str, limit: int = HISTORY_MAXLEN) -> list[tuple[float, float]]:
        """Oldest-first (timestamp, price) points for a ticker.

        Returns an empty list for an untracked ticker rather than raising, so
        callers (e.g. the chart) can draw nothing instead of erroring.
        """
        with self._lock:
            points = self._history.get(ticker)
            if not points:
                return []
            return list(points)[-limit:]

    @property
    def version(self) -> int:
        """Current version counter. Useful for SSE change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
