# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`, plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat"), and `to_dict()` for JSON serialization.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None) -> PriceUpdate`
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `get_history(ticker, limit=HISTORY_MAXLEN) -> list[tuple[float, float]]` — rolling in-memory `(timestamp, price)` points, oldest-first, capped at `HISTORY_MAXLEN` (600, ~5 min at the simulator's 500ms cadence). Empty list for an untracked ticker.
  - `remove(ticker)` — also clears that ticker's history
  - `version` property — monotonic counter, increments on every update (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`.

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
```

Pushes the entire price cache as one JSON object (map keyed by ticker) whenever
`PriceCache.version` changes, polled every 500ms. A connecting client always gets
a full snapshot immediately, including after a reconnect. When the version hasn't
changed for 15s (`KEEPALIVE_SECONDS`), an SSE comment line (`: ping`) is sent so
proxies and the frontend's connection indicator don't mistake a quiet market for
a dead connection.

### Price History

```python
from app.market import create_history_router

router = create_history_router(price_cache)  # Returns FastAPI APIRouter
# Endpoint: GET /api/prices/{ticker}/history?limit=600
```

Backs the main chart's initial backfill from `PriceCache`'s rolling in-memory
history. Not persisted — a restart clears it, matching the simulator's own
reset-to-seed behavior.

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
