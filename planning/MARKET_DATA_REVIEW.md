# Market Data Backend — Code Review

**Date:** 2026-09-02
**Scope:** `backend/app/market/` (8 source files, 730 LOC) and `backend/tests/market/` (7 test files, 1,161 LOC)
**Reviewer:** Claude, in response to issue #5

---

## 1. Test Execution — Could Not Run

This review's environment does not have permission to execute shell commands that
run the Python interpreter or install dependencies (`uv sync`, `uv run pytest`, even
`python3 -c ...` are all blocked pending approval, and this run has no human
available to approve them). This is the same limitation `planning/MARKET_DATA_SUMMARY.md`
recorded on the previous pass. **No test was executed as part of this review.**

To get a real pass/fail signal, re-run this task with `Bash(uv sync:*)` and
`Bash(uv run:*)` added to the allowed tools, or run locally:

```bash
cd backend
uv sync --extra dev
uv run --extra dev pytest -v --cov=app --cov-report=term-missing
uv run --extra dev ruff check app/ tests/
```

In place of execution, every test file was read in full and traced by hand against
the source it exercises (see §4). All 96 tests found in the suite exercise real
code paths correctly as far as static reading can confirm — no test asserts on
behavior the source doesn't actually implement, and no test's mocking hides a
divergence between the mock's shape and the real one (the concern that let a
prior bug through at 94% coverage, per `test_massive.py`'s own docstring).

**Test count:** 96 across 7 files (`test_models.py` 11, `test_cache.py` 24,
`test_simulator.py` 17, `test_simulator_source.py` 10, `test_factory.py` 7,
`test_massive.py` 17, `test_stream.py` 15 by count of `def test_`/`async def test_`
— slightly higher than the 94 recorded in `MARKET_DATA_SUMMARY.md` §"Test Suite",
consistent with incremental additions since that doc was last updated).

---

## 2. Architecture Assessment

The module is well-factored and matches `planning/MARKET_DATA_DESIGN.md` and
`planning/MARKET_DATA_SUMMARY.md` closely:

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBMSimulator (Cholesky-correlated GBM)
└── MassiveDataSource    →  Polygon.io REST poller
        │
        ▼
   PriceCache (thread-safe, latest price + 600-point rolling history)
        │
        ├──→ create_stream_router()  → GET /api/stream/prices (SSE, with keepalive)
        └──→ create_history_router() → GET /api/prices/{ticker}/history
```

**Strengths confirmed by this pass:**

- Strategy pattern cleanly isolates the two data sources behind `MarketDataSource`; nothing downstream needs to know which is active.
- `PriceUpdate` is `frozen=True, slots=True` — correct choice for a value object shared across threads/tasks.
- `PriceCache` centralizes all locking (`threading.Lock`) around the one mutable structure producers and consumers touch; the API surface (`update`, `get`, `get_all`, `get_price`, `remove`, `get_history`) is small and each method acquires the lock exactly once.
- The GBM math is textbook-correct log-normal price evolution, and the `dt` sizing (`0.5s / (252 * 6.5h * 3600s)`) is derived, not guessed, with the derivation left in a comment.
- Cholesky-based correlated draws (`simulator.py:84-90`) are a genuinely nice touch for a simulator whose only job is to look convincing on a chart.
- The three TODOs recorded as open in `PLAN.md` §13 (SSE keepalive, rolling history, `/history` endpoint) are all implemented and each has direct test coverage (`test_stream.py`).
- The two defects `MARKET_DATA_DESIGN.md` §8.4 recorded against the Massive client (wrong attribute name, nanoseconds-as-milliseconds) are fixed in `massive_client.py:130-136`, and `test_massive.py` deliberately builds real `TickerSnapshot` objects via `TickerSnapshot.from_dict(...)` rather than `MagicMock`, which is exactly the right defense against that class of bug recurring silently.
- `pyproject.toml` already has `[tool.hatch.build.targets.wheel] packages = ["app"]` — the "High" build-breaking bug from the archived 2026-02-10 review (`planning/archive/MARKET_DATA_REVIEW.md` §3.1) is fixed.
- `massive` is a top-level import now (`massive_client.py:9-11`), not a lazy one — the archived review's §3.2 concern about tests being fragile without the package installed no longer applies; `pyproject.toml` lists it as a core dependency.

---

## 3. Issues Found

### 3.1 `create_stream_router` / `create_history_router` mutate a shared module-level router (Severity: Medium)

`stream.py:18-19` defines `router` and `history_router` at module scope. Both
factory functions register their route via a closure on these **same shared
objects** rather than creating a fresh `APIRouter()` per call:

```python
router = APIRouter(prefix="/api/stream", tags=["streaming"])
history_router = APIRouter(prefix="/api/prices", tags=["prices"])

def create_stream_router(price_cache: PriceCache) -> APIRouter:
    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        ...
    return router
```

Calling either factory more than once appends another route to the same
underlying router rather than returning an independent one. This was flagged
as a "latent footgun for testing" in the archived review (§3.6) when there
were no tests exercising it; now there are, and it is no longer latent:
`test_stream.py`'s `_history_endpoint()` helper calls `create_history_router(cache)`
fresh in **six different tests**, so `history_router` in the running test
process accumulates six duplicate `/{ticker}/history` routes by the end of
the file. The tests still pass because they grab `router.routes[-1].endpoint`
— the most recently registered one — but this only works by coincidence of
ordering, not because the router is actually being rebuilt.

The real risk is downstream: once this module is wired into the FastAPI app
(the next piece of work per `PLAN.md` §13 "Still open"), any test that builds
the app more than once per process — a very common pytest pattern (an `app`
fixture instantiated per test, or per module) — will silently accumulate
duplicate routes on every rebuild, since `router`/`history_router` are shared
mutable module state that outlives any single app instance.

**Fix:** construct a new `APIRouter()` inside each factory function instead of
reusing a module-level instance:

```python
def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        ...
    return router
```

### 3.2 `PriceCache.update()` treats a falsy timestamp as "no timestamp given" (Severity: Low)

```python
ts = timestamp or time.time()
```

(`cache.py:40`) A caller that explicitly passes `timestamp=0.0` (Unix epoch,
1970-01-01) gets `time.time()` substituted instead, because `0.0` is falsy.
No current caller does this — `massive_client.py` only reaches this path with
`time.time()` already substituted upstream when `sip_timestamp` is falsy — so
this is not exploitable today, but it is a latent correctness gap for any
future caller (e.g., a test replaying historical data from epoch-adjacent
timestamps, or a backfill script). Prefer `timestamp if timestamp is not None
else time.time()`.

### 3.3 `MassiveDataSource`'s poller task dies silently on `AuthError` (Severity: Low)

`_poll_once()` deliberately re-raises `AuthError` (`massive_client.py:103-105`)
with the comment "unrecoverable: do not retry on a loop" — a reasonable
choice. But the only place that awaits `self._task` is `stop()`
(`massive_client.py:60-69`), which nothing calls until shutdown. If the key
is revoked *after* `start()` succeeds (rather than being bad from the first
poll), the background task raised inside `_poll_loop()` simply stops running;
asyncio logs "Task exception was never retrieved" at some later point (often
at garbage collection, easy to miss in container logs), and the app has no
other signal that live prices have silently frozen. `test_auth_error_propagates`
confirms the exception propagates out of `_poll_once()`, but there is no test
for what happens to `_poll_loop()` or the app once that happens.

This is fine as coded for now since nothing outside the market module reads
task health yet, but whoever wires this into the app (`PLAN.md` §13, item 3)
should either attach a `Task.add_done_callback` that logs loudly / flips a
health flag, or have `GET /api/health` report `market_source` as degraded
when the task is dead. Worth a one-line note in `MARKET_DATA_SUMMARY.md` so
it isn't forgotten during integration.

### 3.4 `PriceCache.version` property reads outside the lock (Severity: Trivial)

Unchanged from the archived review's §3.4: `cache.py:94-97` reads `self._version`
without acquiring `self._lock`. Safe under CPython's GIL for a single `int`
read, inconsistent with the rest of the class, and only a real concern on a
no-GIL build. Not worth blocking on, but a two-line fix if anyone is passing
through this file for another reason.

### 3.5 `market_data_demo.py` and `backend/README.md` are outside the reviewed test scope but were not separately verified

The demo script (`market_data_demo.py`, 205 lines) is referenced by
`MARKET_DATA_SUMMARY.md` as a manual verification tool and has no automated
test coverage, which is appropriate for a Rich terminal demo — flagging only
so it's clear this review's "all tests pass" scope is `backend/tests/market/`,
not the demo script.

---

## 4. Test Suite Assessment (by module)

| Module | File | Assessment |
|---|---|---|
| `models.py` | `test_models.py` (11 tests) | Complete: creation, `change`/`change_percent`/`direction` in both directions, zero-previous-price edge case, `to_dict()` shape, and frozen-dataclass immutability. No gaps. |
| `cache.py` | `test_cache.py` (24 tests) | Thorough. Covers direction transitions, `version` monotonicity, `__len__`/`__contains__`, price rounding, custom timestamps, and a dedicated `TestPriceHistory` class covering bounding, ordering, per-ticker isolation, limit-narrower-than-stored, and that `remove()` clears history without touching other tickers. No test for concurrent multi-thread writes (the lock is exercised only single-threaded) — the archived review flagged this as missing in §4.2 and it remains missing; low priority since the logic is simple enough to verify by inspection. |
| `interface.py` | (no dedicated file; exercised transitively via simulator/massive tests) | Reasonable — it's an ABC with no logic of its own. |
| `seed_prices.py` | `test_simulator.py`, `test_factory.py` (transitively) | No dedicated test file, but every constant is exercised indirectly through `GBMSimulator` tests (`_pairwise_correlation` tests cover tech/finance/TSLA/cross-sector explicitly). Fine given it's pure data. |
| `simulator.py` | `test_simulator.py` (17), `test_simulator_source.py` (10) | Strong. `GBMSimulator`: positivity over 10,000 steps, seed matching, add/remove (including duplicate/nonexistent no-ops), unknown-ticker random seeding, Cholesky construction/teardown on ticker count crossing 1↔2, all four correlation branches, `dt` sanity, and rounding. `SimulatorDataSource`: cache population on start, periodic updates via real `asyncio.sleep`, idempotent stop, dynamic add/remove, empty-start, and exception resilience. The timing-based assertions (`asyncio.sleep(0.3)` then assert version advanced) are inherently a little flaky under CI load, but the margins used (3-6x the interval) are generous enough to be low-risk. |
| `massive_client.py` | `test_massive.py` (17) | Strong, and specifically hardened against the exact bug class that shipped previously — `_apply_snapshots` is tested against real `TickerSnapshot.from_dict(...)` objects, not mocks, for timestamp conversion, missing-trade skipping, mixed valid/invalid batches, and multi-ticker updates. Polling lifecycle covers success, `BadResponse` (swallowed), `AuthError` (re-raised, see §3.3), generic exceptions (swallowed), ticker add/remove with normalization, and start/stop idempotency. No gap of consequence. |
| `stream.py` | `test_stream.py` (15) | Was 31% covered and untested in the archived review; now has direct coverage of the async generator via a hand-rolled `FakeRequest`, including the retry directive, snapshot-on-connect (and thus reconnect), the frozen payload field set, keepalive timing (via `monkeypatch` on `KEEPALIVE_SECONDS` rather than a real 15s wait — good practice), a fresh data event following a ping, disconnect handling, and the empty-cache case. `create_history_router`'s endpoint is tested for ordering, unknown-ticker empty response, normalization, and limit clamping in both directions. The one real gap is architectural, not a missing test: see §3.1 — the tests would catch a *regression* in behavior but not the router-reuse issue itself, since grabbing `routes[-1]` happens to paper over it. |
| `factory.py` | `test_factory.py` (7) | Complete for its size: unset/empty/whitespace-only key → simulator, set key → Massive, and that both branches thread the cache reference through correctly. |

**Net assessment:** the suite is comprehensive and, importantly, methodologically
careful — the deliberate choice to build real `TickerSnapshot` objects instead of
`MagicMock` in `test_massive.py` is the single best thing about this test suite,
since it's precisely what would have caught the `last_trade.timestamp` /
`sip_timestamp` bug the archived review found. No test was found asserting
something the source doesn't do, and no source behavior of consequence lacks a
test, with the caveats above (concurrency, and the router-reuse issue masked
by test ordering).

---

## 5. Comparison Against the Prior Review

`planning/archive/MARKET_DATA_REVIEW.md` (2026-02-10) recorded 7 issues. Status now:

| # | Issue | Status |
|---|---|---|
| 3.1 | Missing hatchling wheel config | **Fixed** |
| 3.2 | Massive tests fragile without the `massive` package | **Fixed** (now a core dependency, imported at module level) |
| 3.3 | `_generate_events` return type `-> None` instead of `AsyncGenerator` | **Fixed** (`stream.py:87`) |
| 3.4 | `PriceCache.version` reads outside the lock | **Still open** (§3.4 above, trivial) |
| 3.5 | `SimulatorDataSource.get_tickers` reached into `GBMSimulator._tickers` | **Fixed** — `GBMSimulator.get_tickers()` now exists (`simulator.py:140-142`) and is used |
| 3.6 | Module-level router registered on repeated calls | **Still open, and now demonstrated by the test suite itself** (§3.1 above, upgraded to Medium given it will bite during app integration) |
| 3.7 | Unused imports in tests | **Fixed** — no unused `pytest`/`math`/`asyncio` imports found in any current test file |

Also confirmed fixed: the two Massive parsing defects `MARKET_DATA_DESIGN.md`
§8.4 described (wrong attribute name, nanosecond/millisecond confusion), and
all three items `PLAN.md` §13 listed as open TODOs (rolling history, `/history`
endpoint, SSE keepalive).

---

## 6. Verdict

The market data backend is in good shape and ready to be built on. Of the two
open items:

- **§3.1 (shared module-level router)** should be fixed before the FastAPI
  `lifespan` wiring work begins (`PLAN.md` §13, item 3) — it's a small,
  mechanical fix (stop reusing module-level `router`/`history_router`; build
  one per call) and doing it now avoids a confusing bug later when the app
  factory is instantiated more than once, which is standard practice for
  backend test fixtures.
- **§3.2/§3.4 (falsy-timestamp substitution, unlocked version read)** are
  low-risk and can be picked up opportunistically.
- **§3.3 (silent poller death on revoked key)** is a design note for whoever
  adds the `GET /api/health` endpoint — surface poller liveness there.

None of these block downstream work. **Tests were not executed in this pass**
due to environment permissions (§1) — that is the one action item this review
could not complete, and it should be re-run with `uv`/`python3` execution
permitted to get an authoritative pass/fail/coverage number rather than the
static analysis this document is based on.
