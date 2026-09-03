# Market Data Backend — Summary

**Status:** Complete, tested, reviewed, all issues resolved. Includes the rolling price
history, SSE keepalive, and corrected Massive parsing that `MARKET_DATA_DESIGN.md`
identified as outstanding — see "Gaps Closed" below.

## What Was Built

A complete market data subsystem in `backend/app/market/` (8 modules) providing live price simulation and real market data via a unified interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Polygon.io REST poller (when MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory, latest price + rolling history)
        │
        ├──→ SSE stream endpoint (/api/stream/prices, with keepalive)
        ├──→ Price history endpoint (/api/prices/{ticker}/history)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate` — immutable frozen dataclass (ticker, price, previous_price, timestamp, change, direction) |
| `interface.py` | `MarketDataSource` — abstract base class defining `start/stop/add_ticker/remove_ticker/get_tickers` |
| `cache.py` | `PriceCache` — thread-safe price store with version counter for SSE change detection, plus a bounded per-ticker `(timestamp, price)` history (`get_history`) |
| `seed_prices.py` | Realistic seed prices, per-ticker GBM params (drift/volatility), correlation groups |
| `simulator.py` | `GBMSimulator` (Geometric Brownian Motion with Cholesky-correlated moves) + `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` — REST polling client for Polygon.io via the `massive` package |
| `factory.py` | `create_market_data_source()` — selects simulator or Massive based on `MASSIVE_API_KEY` env var |
| `stream.py` | `create_stream_router()` — SSE endpoint with keepalive; `create_history_router()` — price history endpoint |

### Key Design Decisions

- **Strategy pattern** — both data sources implement the same ABC; downstream code is source-agnostic
- **PriceCache as single point of truth** — producers write, consumers read; no direct coupling
- **GBM with correlated moves** — Cholesky decomposition of sector-based correlation matrix; tech stocks correlate at 0.6, finance at 0.5, cross-sector at 0.3
- **Random shock events** — ~0.1% chance per tick per ticker of a 2-5% move for visual drama
- **SSE over WebSockets** — simpler, one-way push, universal browser support
- **SSE keepalive** — a `: ping` comment line after 15s without a version change, so a Massive-backed feed (15s polls) doesn't idle-timeout through proxies
- **Rolling price history in `PriceCache`** — a 600-point bounded deque per ticker (~5 min at the simulator's 500ms cadence), deliberately not persisted, so the main chart backfills instantly on ticker selection instead of drawing from scratch

## Gaps Closed

`planning/MARKET_DATA_DESIGN.md` §0 recorded four outstanding gaps against the code as it stood
on 2026-09-01. All four are now closed:

1. **Massive client wrote nothing to the cache.** `last_trade.timestamp` does not exist on the
   real `TickerSnapshot` model (the attribute is `sip_timestamp`), and the divisor treated
   nanoseconds as milliseconds even when corrected. The parse loop is now `_apply_snapshots()`,
   using `trade.sip_timestamp / NANOS_PER_SECOND` and guarding on `is None` rather than catching
   `AttributeError` — tested against the real `TickerSnapshot.from_dict(...)` model, not a
   `MagicMock`, which is what let the original bug ship at 94% coverage.
2. **`PriceCache` rolling history** — added (`get_history`, bounded deque, cleared on `remove`).
3. **`GET /api/prices/{ticker}/history`** — added via `create_history_router()`.
4. **SSE keepalive** — added (`: ping` every 15s of idle version).

The fifth item in the design doc — wiring the market module into a FastAPI `lifespan` alongside
the database, portfolio, and watchlist routes — is intentionally **not** included here. It depends
on those other components, which per the root `CLAUDE.md` are still to be built.

## Test Suite

**103 tests across 7 modules** in `backend/tests/market/`, all passing with **99% coverage**
(verified by actually running `uv run --extra dev pytest --cov=app --cov-report=term-missing`;
prior passes here were static-only due to sandbox permission limits).

| Module | Tests | Notes |
|--------|-------|-------|
| test_models.py | 11 | |
| test_cache.py | 19 | +1 for the falsy-timestamp fix below |
| test_simulator.py | 17 | |
| test_simulator_source.py | 10 | integration tests |
| test_factory.py | 7 | |
| test_massive.py | 21 | Parsing tests built against the real `TickerSnapshot` model instead of `MagicMock`; +3 for the poller-health fix below |
| test_stream.py | 17 | SSE generator (snapshot-on-connect, keepalive, disconnect), the history endpoint, and +2 for the router-factory fix below |

`uv run --extra dev ruff check app/ tests/` passes clean.

## Code Review & Fixes Applied

Two review passes. The first (archived, 2026-02-10) found 7 issues, all resolved — see the prior
revision of this file. `planning/MARKET_DATA_REVIEW.md` (2026-09-02) found 6 more against the
completed module; all are now resolved:

1. **Shared module-level router (`stream.py`, Medium)** — `create_stream_router()` and
   `create_history_router()` built their route onto a shared `router`/`history_router` object at
   module scope, so calling either factory more than once per process (a normal pytest `app`
   fixture pattern) silently accumulated duplicate routes. Each factory now constructs a fresh
   `APIRouter()` per call. Regression-tested in `test_stream.py::TestRouterFactoriesReturnFreshRouters`.
2. **Falsy-timestamp substitution (`cache.py`, Low)** — `PriceCache.update()` used
   `timestamp or time.time()`, which silently replaces an explicit `timestamp=0.0` (a legitimate
   Unix epoch instant) because `0.0` is falsy. Changed to `timestamp if timestamp is not None else
   time.time()`. Tested in `test_cache.py::test_epoch_zero_timestamp_is_not_replaced`.
3. **Silent poller death on a revoked key (`massive_client.py`, Low)** — if `AuthError` is raised
   from inside the background poll loop (as opposed to during `start()`), nothing awaits the task
   until `stop()`, so live prices silently freeze with only an easy-to-miss "Task exception was
   never retrieved" log at GC time. `MassiveDataSource` now attaches a `Task.add_done_callback`
   that logs the failure loudly and flips a new `is_healthy` property to `False` (deliberate
   cancellation via `stop()` does not flip it) — ready for a future `GET /api/health` to report
   `market_source` as degraded. Tested in `test_massive.py` (`test_is_healthy_*`).
4. **`PriceCache.version` read outside the lock (`cache.py`, Trivial)** — the property now
   acquires `self._lock` like every other accessor, for consistency (safe under CPython's GIL
   regardless, but only a real concern on a no-GIL build).
5. **Prior review's 7 findings (pyproject build config, lazy imports, SSE return type, public
   `get_tickers()`, correlation constants, unused test imports, massive test mocks)** — unchanged
   from before, still resolved.
6. Two lower-priority notes from the review were left as-is per its own verdict: no concurrent
   multi-thread write test for `PriceCache` (the locking is simple enough to verify by inspection),
   and the demo script `market_data_demo.py` remains outside automated test scope (a Rich terminal
   demo, appropriately so).

## Demo

A Rich terminal demo is available at `backend/market_data_demo.py`:

```bash
cd backend
uv run market_data_demo.py
```

Displays a live-updating dashboard with all 10 tickers, sparklines, color-coded direction arrows, and an event log for notable price moves. Runs 60 seconds or until Ctrl+C.

## Usage for Downstream Code

```python
from app.market import PriceCache, create_market_data_source, create_stream_router, create_history_router

# Startup
cache = PriceCache()
source = create_market_data_source(cache)  # Reads MASSIVE_API_KEY
await source.start(["AAPL", "GOOGL", "MSFT", ...])

app.include_router(create_stream_router(cache))    # GET /api/stream/prices (SSE)
app.include_router(create_history_router(cache))   # GET /api/prices/{ticker}/history

# Read prices
update = cache.get("AAPL")          # PriceUpdate or None
price = cache.get_price("AAPL")     # float or None
all_prices = cache.get_all()        # dict[str, PriceUpdate]
history = cache.get_history("AAPL") # [(timestamp, price), ...] oldest-first, [] if untracked

# Dynamic watchlist
await source.add_ticker("TSLA")
await source.remove_ticker("GOOGL")

# Shutdown
await source.stop()
```
