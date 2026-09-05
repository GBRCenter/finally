# MARKET_INTERFACE.md — The Unified Market Data API

How FinAlly retrieves stock prices from either the Massive API or the built-in simulator through one interface, selected by whether `MASSIVE_API_KEY` is set.

Companion documents: `MASSIVE_API.md` (the real data provider) and `MARKET_SIMULATOR.md` (the fallback). This document is the contract between them and the rest of the backend.

**Status:** the core of this design is implemented in `backend/app/market/`. Sections marked **TODO** are specified but not yet built.

---

## 1. The problem this solves

Two data sources with nothing in common:

- **Massive** — a synchronous HTTP client, polled every 15 seconds, returning whatever the exchanges last reported, with gaps for unknown tickers and frozen values overnight.
- **The simulator** — a pure in-process computation, stepping every 500ms, always alive, and able to invent a plausible price for any symbol.

Everything downstream — SSE streaming, portfolio valuation, trade execution, the P&L snapshot task — must not care which one is running. A trade fills at "the current price of AAPL" whether that price came from NASDAQ or from a random number generator.

The design achieves that with **one indirection and one shared buffer**:

```
                       writes                      reads
  ┌──────────────────┐        ┌────────────┐              ┌─────────────────────┐
  │ SimulatorSource  │───┐    │            │──────────────│ SSE  /api/stream    │
  │   (500ms step)   │   ├───▶│ PriceCache │──────────────│ Portfolio valuation │
  ├──────────────────┤   │    │  (in-mem,  │──────────────│ Trade execution     │
  │ MassiveSource    │───┘    │thread-safe)│──────────────│ Snapshot task       │
  │   (15s poll)     │        │            │──────────────│ /api/prices/history │
  └──────────────────┘        └────────────┘              └─────────────────────┘
      MarketDataSource
     (abstract interface)
```

The critical property: **nothing downstream ever calls the data source to get a price.** Sources are write-only from the application's point of view; readers only ever touch the cache. That is what makes the two implementations substitutable despite a 30× difference in update cadence.

### Module map — `backend/app/market/`

| File | Contents |
|---|---|
| `models.py` | `PriceUpdate` — the single price record |
| `cache.py` | `PriceCache` — the shared buffer |
| `interface.py` | `MarketDataSource` — the abstract contract |
| `simulator.py` | `GBMSimulator`, `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` |
| `factory.py` | `create_market_data_source` — the selection rule |
| `seed_prices.py` | Simulator constants |
| `stream.py` | The SSE endpoint |

---

## 2. `PriceUpdate` — the unit of data

An immutable, frozen dataclass. Both sources produce it; every reader consumes it.

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)   # Unix epoch SECONDS
```

`change`, `change_percent`, and `direction` are computed properties, not stored fields — they cannot drift out of sync with the prices they describe.

```python
@property
def change(self) -> float:
    return round(self.price - self.previous_price, 4)

@property
def change_percent(self) -> float:
    if self.previous_price == 0:
        return 0.0
    return round((self.price - self.previous_price) / self.previous_price * 100, 4)

@property
def direction(self) -> str:
    if self.price > self.previous_price:
        return "up"
    elif self.price < self.previous_price:
        return "down"
    return "flat"
```

### Two frozen conventions

`to_dict()` is the SSE wire format and is **frozen** — the shipped frontend contract depends on it (`PLAN.md` §6):

- **`timestamp` is Unix epoch seconds as a float**, never ISO. The frontend multiplies by 1000 for `Date`. Massive's nanosecond and millisecond timestamps are converted at the boundary — see `MASSIVE_API.md` §7.
- **`change_percent` is already in percent units.** `0.021` means 0.021%, not 2.1%. Note this deliberately differs from REST responses elsewhere in the API, where percentages are fractions (`PLAN.md` §8). The inconsistency is real; it is preserved because the market module shipped first and the frontend was written against it.

`previous_price` means *the price at the previous update*, not the previous session's close. On the first update for a ticker it equals `price`, so `direction` is `"flat"` and `change` is `0.0` — a new ticker never flashes green or red on its first tick.

---

## 3. `PriceCache` — the shared buffer

An in-memory `dict` behind a `threading.Lock`, plus a monotonic version counter.

```python
class PriceCache:
    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate
    def get(self, ticker: str) -> PriceUpdate | None
    def get_all(self) -> dict[str, PriceUpdate]     # shallow copy
    def get_price(self, ticker: str) -> float | None
    def remove(self, ticker: str) -> None
    @property
    def version(self) -> int
    def __len__(self) -> int
    def __contains__(self, ticker: str) -> bool
```

Three design points that matter:

**`update()` derives `previous_price` itself.** Callers pass only the new price; the cache looks up what it had and constructs the `PriceUpdate`. Neither source needs to track prior state for the purpose of computing a delta, and the two cannot implement it differently.

**A `threading.Lock`, not an `asyncio.Lock`.** `MassiveDataSource` writes from an `asyncio.to_thread` worker, so a genuine cross-thread lock is required. The critical sections are a few dict operations, so contention is irrelevant.

**`version` increments on every write and is the SSE change-detection mechanism.** The stream compares it every 500ms rather than diffing prices. Because a fresh generator starts at `last_version = -1`, the first comparison always differs, so **every connecting client immediately receives a full snapshot** — including after a reconnect. This is why no separate snapshot endpoint exists.

`get_all()` returns a shallow copy; since `PriceUpdate` is frozen, the copy is effectively deep and safe to iterate outside the lock.

### Rolling price history — **TODO**

`PLAN.md` §6 requires the main chart to be populated the instant a ticker is clicked. `PriceCache` gains a bounded per-ticker deque of `(timestamp, price)`:

```python
from collections import deque

HISTORY_MAXLEN = 600      # ~5 minutes at the 500ms simulator cadence

self._history: dict[str, deque[tuple[float, float]]] = {}
```

- Appended inside `update()`, under the same lock.
- `deque(maxlen=600)` evicts the oldest point automatically — no pruning logic.
- `remove()` must drop the ticker's deque too, or removed tickers leak.
- Deliberately **not persisted**. A restart clears it, which is the honest behaviour for a simulator with no real history.

Memory is negligible: 600 points × 50 tickers × ~16 bytes ≈ 500KB.

New reader, backing `GET /api/prices/{ticker}/history?limit=600`:

```python
def get_history(self, ticker: str, limit: int = 600) -> list[tuple[float, float]]:
    """Oldest-first (timestamp, price) points. Empty list for an untracked ticker."""
    with self._lock:
        points = self._history.get(ticker)
        if not points:
            return []
        return list(points)[-limit:]
```

An untracked ticker returns `[]`, not a 404 — the chart draws nothing rather than erroring (`PLAN.md` §8).

Under Massive the deque fills at one point per 15-second poll, so five minutes of wall time is 20 points rather than 600. The chart is sparse but correct. Backfilling from `get_aggs` (`MASSIVE_API.md` §5) is the eventual upgrade.

---

## 4. `MarketDataSource` — the abstract contract

```python
class MarketDataSource(ABC):
    @abstractmethod
    async def start(self, tickers: list[str]) -> None: ...
    @abstractmethod
    async def stop(self) -> None: ...
    @abstractmethod
    async def add_ticker(self, ticker: str) -> None: ...
    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None: ...
    @abstractmethod
    def get_tickers(self) -> list[str]: ...
```

Five methods, and every one is about *lifecycle and membership* — none of them returns a price. That absence is the whole design. A `get_price()` on this interface would tempt callers into a per-request API hit under Massive and would make the two implementations behave differently under load.

### Behavioural contract

Binding on both implementations. A test suite that passes against one should pass against the other.

| Method | Guarantee |
|---|---|
| `start(tickers)` | Begins a background task writing to the cache. **Seeds the cache before returning**, so the first SSE event is never empty. Called exactly once; calling twice is undefined. |
| `stop()` | Cancels the task and releases resources. **Idempotent.** No writes to the cache afterwards. |
| `add_ticker(t)` | Adds to the tracked set. No-op if present. Simulator seeds a price immediately; Massive picks it up on the next poll. |
| `remove_ticker(t)` | Removes from the tracked set **and from the cache**. No-op if absent. |
| `get_tickers()` | Current tracked set. Synchronous — it reads local state only. |

Two asymmetries are permitted and must not be papered over:

- **Seeding latency.** `add_ticker` on the simulator makes a price available immediately; on Massive it takes up to one poll interval. The API contract already accommodates this — `GET /api/watchlist` returns `price: null` until the first tick, and the UI shows `—`.
- **Cadence.** 500ms versus 15s. Readers must never assume a minimum update rate. This is exactly what the SSE keepalive in §7 exists to handle.

### `remove_ticker` also clears the cache — and why that is dangerous

Both implementations call `self._cache.remove(ticker)`. That is correct for the interface but makes the method destructive: a held position whose ticker is removed loses its price, and with it its valuation, its P&L, its heatmap tile, and its snapshot contribution. §5 is the rule that prevents it.

---

## 5. Which tickers are tracked

**The tracked set is `watchlist ∪ {tickers with a non-zero position}`.**

The two sets diverge the moment a user buys TSLA and then removes it from the watchlist. The position still needs a live price. This rule is the single most important piece of integration logic in the module, because getting it wrong produces a silently frozen position rather than an error.

| Trigger | Action |
|---|---|
| `POST /api/watchlist` | always `await source.add_ticker(t)` |
| `DELETE /api/watchlist/{t}` | `await source.remove_ticker(t)` **only if no position in `t` is held** |
| Buy a ticker not currently tracked | `await source.add_ticker(t)` as part of trade execution |
| Sell a position to zero | if `t` is not on the watchlist, `await source.remove_ticker(t)` |
| `POST /api/reset` | re-sync the tracked set to exactly the ten default tickers |

A single helper keeps the rule in one place rather than at four call sites:

```python
async def untrack_if_unused(source: MarketDataSource, ticker: str) -> None:
    """Stop tracking a ticker only if it is neither watched nor held."""
    if is_on_watchlist(ticker) or has_position(ticker):
        return
    await source.remove_ticker(ticker)
```

### Startup

```python
tickers = sorted(set(get_watchlist_tickers()) | set(get_position_tickers()))
await source.start(tickers)
```

Reading both tables at startup — not just the watchlist — is what makes a position held across a restart come back with a live price.

---

## 6. Selection — `create_market_data_source`

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the market data source indicated by the environment.

    MASSIVE_API_KEY set and non-empty -> MassiveDataSource (real data)
    otherwise                         -> SimulatorDataSource (GBM simulation)

    Returns an unstarted source; the caller must await source.start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        logger.info("Market data source: Massive API (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)

    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

`.strip()` before the truth test is deliberate: `.env` files routinely contain `MASSIVE_API_KEY=` or a stray space, and a whitespace-only key would otherwise select the Massive path and then fail every poll with a 401. Empty means empty.

**The simulator is the default, and the fallback is decided once at startup — never at runtime.** A source that silently switched to the simulator after a Massive outage would show users invented prices while they believed they were seeing the market. Rejected keys and failed polls are logged; they do not change the source. `GET /api/health` reports which one is live:

```json
{"status": "ok", "market_source": "simulator", "llm_mock": false}
```

Returning an **unstarted** source keeps construction synchronous and lets the caller decide the ticker set from the database — the factory has no business reading tables.

---

## 7. Lifecycle and wiring

One `PriceCache` and one source per process, owned by the FastAPI lifespan.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.market import PriceCache, create_market_data_source, create_stream_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache)

    tickers = sorted(set(get_watchlist_tickers()) | set(get_position_tickers()))
    await source.start(tickers)

    app.state.price_cache = cache
    app.state.market_source = source
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(app.state.price_cache))
```

The cache and source are passed explicitly (via router factories or `app.state`) rather than held in module globals, which is what keeps tests able to construct an isolated cache per test.

**Ordering, from `PLAN.md` §11:** mount all `/api/*` routers *before* the static file mount. A `StaticFiles(html=True)` mount at `/` registered first shadows every endpoint, including the SSE stream.

### The SSE stream

`GET /api/stream/prices`, `Content-Type: text/event-stream`. The generator opens with `retry: 1000`, then pushes the **entire cache as one JSON object** whenever `version` changes, polled every 500ms:

```
retry: 1000

data: {"AAPL": {"ticker": "AAPL", "price": 190.52, "previous_price": 190.48, "timestamp": 1755873791.482, "change": 0.04, "change_percent": 0.021, "direction": "up"}, "GOOGL": {...}}
```

One event carries every ticker — not one event per ticker. The client replaces its price map wholesale, so there is no merge logic and no missed-update reconciliation.

### Keepalive — **TODO**

When the version has not changed for 15 seconds, emit an SSE comment:

```python
KEEPALIVE_SECONDS = 15.0

last_sent = time.monotonic()
while True:
    if await request.is_disconnected():
        break

    current_version = price_cache.version
    if current_version != last_version:
        last_version = current_version
        prices = price_cache.get_all()
        if prices:
            payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
            yield f"data: {payload}\n\n"
            last_sent = time.monotonic()
    elif time.monotonic() - last_sent >= KEEPALIVE_SECONDS:
        yield ": ping\n\n"
        last_sent = time.monotonic()

    await asyncio.sleep(interval)
```

Without this, a Massive-backed feed sends no bytes between 15-second polls. That idle-times-out through proxies and leaves the frontend unable to distinguish a quiet market from a dead connection. The connection indicator — green on `onopen`, yellow on `onerror`, red after a ping gap beyond ~40 seconds — depends on it.

---

## 8. Implementing a new source

The interface is small enough that a third source is a contained piece of work. The checklist:

1. Subclass `MarketDataSource` and implement all five methods.
2. `start()` must **seed the cache before returning**.
3. Never write to the cache after `stop()`; make `stop()` idempotent.
4. `remove_ticker()` must call `cache.remove(ticker)`.
5. Convert timestamps to **Unix epoch seconds as a float** at the boundary.
6. Never let a fetch error kill the background loop — log and retry on the next cycle.
7. If the source is synchronous, wrap every call in `asyncio.to_thread`.
8. Add a branch to `create_market_data_source` and a value to `market_source` in `/api/health`.

Point 7 is not optional. `massive.RESTClient` is `urllib3`-based and blocking; calling it directly from `async def` stalls the event loop for the duration of the HTTP round trip, which stops the SSE stream and every in-flight request. `MassiveDataSource` gets this right:

```python
snapshots = await asyncio.to_thread(self._fetch_snapshots)
```

### Massive polling, in outline

```python
async def _poll_loop(self) -> None:
    """Poll on interval. The first poll already happened in start()."""
    while True:
        await asyncio.sleep(self._interval)
        await self._poll_once()
```

`start()` performs one poll synchronously before creating the task, so the cache is warm before the first client connects. `_poll_interval` defaults to 15 seconds to stay inside the free tier's 5 requests/minute (`MASSIVE_API.md` §2); paid tiers can drop to 2–5 seconds.

> The `_poll_once` parsing in `massive_client.py` currently reads a non-existent attribute and uses the wrong unit divisor, which means the Massive path writes nothing to the cache at all. Both defects are reproduced and the corrected parse is given in `MASSIVE_API.md` §8. Fixing them is a prerequisite to the Massive path working.

---

## 9. Testing

Existing coverage: **73 tests passing at 91%** for the market module, measured by running the suite while writing this document. (`MARKET_DATA_SUMMARY.md` still quotes 84%, which is stale.)

**The cache and the interface can be tested without either real source.** A stub is a few lines, and it is the right tool for testing the tracked-ticker rules:

```python
class StubDataSource(MarketDataSource):
    """Records lifecycle calls; writes nothing on its own."""

    def __init__(self, cache: PriceCache) -> None:
        self._cache = cache
        self._tickers: list[str] = []
        self.started = False

    async def start(self, tickers): self._tickers = list(tickers); self.started = True
    async def stop(self): self.started = False
    async def add_ticker(self, t):
        if t not in self._tickers:
            self._tickers.append(t)
    async def remove_ticker(self, t):
        self._tickers = [x for x in self._tickers if x != t]
        self._cache.remove(t)
    def get_tickers(self): return list(self._tickers)
```

What to cover:

- **Cache** — `previous_price` derivation, first-update `flat`, `version` monotonicity, `remove` clearing both price and history, thread safety under concurrent writers.
- **Factory** — unset, empty, and whitespace-only `MASSIVE_API_KEY` all select the simulator; a real value selects Massive.
- **Tracked set** — removing a watchlist ticker with an open position keeps it in the feed; selling to zero off-watchlist removes it. These are the two regressions that produce a frozen position.
- **Massive parsing** — feed the real `TickerSnapshot.from_dict` a documented payload and assert the cached timestamp lands in the plausible present. Never build these snapshots from `MagicMock`: the existing tests do, which is precisely why 94% coverage of `massive_client.py` still missed both defects (`MASSIVE_API.md` §8.3).
- **SSE** — map-shaped payload, float timestamp, percent-unit `change_percent`, full snapshot on connect, keepalive after 15 idle seconds.
- **History** — deque bounded at 600, oldest-first ordering, `[]` for an untracked ticker.

```bash
cd backend
uv run pytest
uv run pytest --cov=app --cov-report=term-missing
```

---

## 10. Summary

| Concern | Resolution |
|---|---|
| Two sources, one consumer | `MarketDataSource` ABC + shared `PriceCache` |
| Which source | `create_market_data_source`, decided once at startup from `MASSIVE_API_KEY` |
| Default | Simulator — always alive, no key, no rate limit |
| How prices are read | Only from the cache, never from the source |
| Which tickers are live | `watchlist ∪ positions` |
| Timestamp format | Unix epoch seconds (float), converted at each source boundary |
| Update delivery | SSE, full cache per event, on `version` change |
| Blocking I/O | `asyncio.to_thread` at the source, always |
| Outstanding | Rolling history + endpoint, SSE keepalive, the two `massive_client.py` defects |
