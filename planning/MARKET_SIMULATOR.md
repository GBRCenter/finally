# MARKET_SIMULATOR.md — The Market Simulator

The approach and code structure for simulating stock prices when no `MASSIVE_API_KEY` is configured. This is FinAlly's **default** data source, so it is what almost every user will see.

Companion documents: `MARKET_INTERFACE.md` (the abstraction it implements) and `MASSIVE_API.md` (the alternative). Implemented in `backend/app/market/simulator.py` and `backend/app/market/seed_prices.py`.

---

## 1. What it must achieve

The simulator is not a research tool. It exists so that a student who clones the repo and runs one Docker command sees a trading terminal that looks alive, at any hour, on any day, with no account and no API key.

That sets the bar precisely:

| Requirement | Why |
|---|---|
| Visible motion every 500ms | The watchlist flashes green and red; a static grid looks broken |
| Motion at a *plausible* scale | AAPL moving $12 per tick destroys the illusion instantly |
| Prices that stay positive | A stock at −$4 is not a rendering bug the user will forgive |
| Correlated moves | Real tech stocks rise together; independent random walks look obviously fake |
| Occasional drama | A flat five minutes is boring; a sudden 3% drop gives the demo a story |
| Any ticker works | The AI chat can add any symbol; "we don't have that one" would feel broken |
| No external dependency | It must run offline, at 3am, on a weekend |

And explicitly **not** required: predictive value, real historical data, order books, bid-ask spreads, or volume modelling. Nothing in the app consumes them.

---

## 2. The model — Geometric Brownian Motion

GBM is the standard model for equity prices and the one that satisfies the requirements above almost incidentally.

```
S(t + dt) = S(t) · exp( (μ − σ²/2)·dt  +  σ·√dt·Z )
```

| Symbol | Meaning |
|---|---|
| `S(t)` | Current price |
| `μ` | Annualised drift — expected return |
| `σ` | Annualised volatility |
| `dt` | Time step, as a fraction of a trading year |
| `Z` | Standard normal draw, correlated across tickers |

Three properties earn its place here:

**Prices cannot go negative.** The update is multiplicative — `exp(...)` is always positive, so `S` never crosses zero. No clamping, no `max(price, 0.01)` guard, no special case. An additive random walk would need all three.

**Returns scale correctly with time.** Volatility is expressed per *year*, and `√dt` converts it to the tick. Change the tick rate and the price series keeps the same annualised character. The 500ms cadence is a display choice, not a modelling parameter.

**The `−σ²/2` term keeps the drift honest.** Without it, `μ` would not be the expected return of the price — a well-known artefact of the log-normal distribution. It costs one subtraction and makes the parameters mean what they say.

### Sizing `dt`

`dt` is expressed against a **trading** year, not a calendar year — markets are closed most of the time, and using 365×24h would understate per-tick moves by a factor of about 4.5.

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.479e-8
```

252 trading days × 6.5 hours × 3600 seconds. A 500ms tick is therefore `8.479e-8` of a year, and `√dt = 2.912e-4`.

What that produces per tick, for `σ·√dt` at the seed prices:

| Ticker | σ | Per-tick σ | Per-tick $ | Per-minute $ (120 ticks) |
|---|---|---|---|---|
| AAPL | 0.22 | 0.0064% | $0.012 | $0.13 |
| JPM | 0.18 | 0.0052% | $0.010 | $0.11 |
| NVDA | 0.40 | 0.0116% | $0.093 | $1.02 |
| TSLA | 0.50 | 0.0146% | $0.036 | $0.40 |

This is the number that decides whether the simulation looks right. Around a cent or two per tick on a $200 stock means prices are **rounded to 2 decimals into a genuinely different value most ticks** — so the UI flashes constantly — while a minute of drift stays in the tens of cents, which is what a real quote screen looks like. Larger and it reads as a crash; smaller and the grid appears frozen.

---

## 3. Correlation via Cholesky decomposition

Independent draws per ticker would show tech stocks moving in opposite directions half the time. Real markets do not do that, and the eye notices immediately.

The fix is standard: draw `n` independent standard normals, then multiply by the Cholesky factor `L` of the desired correlation matrix `C`, where `C = L·Lᵀ`. The resulting vector has exactly the correlation structure of `C`.

```python
z_independent = np.random.standard_normal(n)
z_correlated = self._cholesky @ z_independent
```

### The correlation structure

Sector membership and coefficients live in `seed_prices.py`, not in the simulator:

```python
CORRELATION_GROUPS = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR    = 0.6    # tech stocks move together
INTRA_FINANCE_CORR = 0.5    # finance stocks move together
CROSS_GROUP_CORR   = 0.3    # between sectors, and for unknown tickers
TSLA_CORR          = 0.3    # TSLA does its own thing
```

Resolved pairwise, first match winning:

```python
@staticmethod
def _pairwise_correlation(t1: str, t2: str) -> float:
    tech = CORRELATION_GROUPS["tech"]
    finance = CORRELATION_GROUPS["finance"]

    # TSLA is in the tech set but behaves independently
    if t1 == "TSLA" or t2 == "TSLA":
        return TSLA_CORR

    if t1 in tech and t2 in tech:
        return INTRA_TECH_CORR
    if t1 in finance and t2 in finance:
        return INTRA_FINANCE_CORR

    return CROSS_GROUP_CORR
```

The TSLA clause is checked first on purpose: TSLA is a member of the tech set for every other purpose, but empirically trades on its own news, and a demo where TSLA visibly decouples from the pack is more convincing than one where everything moves in lockstep.

`CROSS_GROUP_CORR` doubles as the default for any symbol the simulator has never heard of, which is what makes §5 work.

### Rebuilding

`_rebuild_cholesky()` runs on every add and remove — `O(n²)` to build the matrix plus `O(n³)` to factor it, on `n < 50`. That is microseconds, and watchlist edits are a human-speed operation, so caching it would be complexity without benefit.

```python
def _rebuild_cholesky(self) -> None:
    n = len(self._tickers)
    if n <= 1:
        self._cholesky = None      # a single ticker needs no correlation
        return

    corr = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
            corr[i, j] = rho
            corr[j, i] = rho

    self._cholesky = np.linalg.cholesky(corr)
```

`step()` falls back to uncorrelated draws when `_cholesky is None`, which covers both the single-ticker and empty cases.

> **Known risk.** `np.linalg.cholesky` raises `LinAlgError` on a matrix that is not positive definite, and this call is unguarded. The current block structure (0.6 / 0.5 / 0.3) was verified positive definite at 7, 20, and 40 tickers, so it is safe as configured — but raising `INTRA_TECH_CORR` toward 1.0, or adding a group whose intra-group correlation is below the cross-group value, can break positive-definiteness and take down `add_ticker`. Anyone editing these constants should re-run the check in §8.

---

## 4. The tick

`step()` is the hot path — every 500ms, for every ticker.

```python
def step(self) -> dict[str, float]:
    """Advance all tickers by one time step. Returns {ticker: new_price}."""
    n = len(self._tickers)
    if n == 0:
        return {}

    z_independent = np.random.standard_normal(n)
    if self._cholesky is not None:
        z_correlated = self._cholesky @ z_independent
    else:
        z_correlated = z_independent

    result: dict[str, float] = {}
    for i, ticker in enumerate(self._tickers):
        params = self._params[ticker]
        mu, sigma = params["mu"], params["sigma"]

        drift = (mu - 0.5 * sigma**2) * self._dt
        diffusion = sigma * math.sqrt(self._dt) * z_correlated[i]
        self._prices[ticker] *= math.exp(drift + diffusion)

        if random.random() < self._event_prob:
            shock_magnitude = random.uniform(0.02, 0.05)
            shock_sign = random.choice([-1, 1])
            self._prices[ticker] *= 1 + shock_magnitude * shock_sign

        result[ticker] = round(self._prices[ticker], 2)

    return result
```

Two details worth pointing out:

**Full precision is kept internally; only the returned value is rounded.** `self._prices[ticker]` stays a full float. Rounding the stored state would accumulate quantisation error into a slow, systematic drift over thousands of ticks.

**One `standard_normal(n)` call per tick, not `n` calls.** A single vectorised draw feeding one matrix multiply is the reason this stays negligible at 500ms.

### Random events

```python
event_probability: float = 0.001    # per ticker, per tick
```

A 2–5% jump in either direction. With 10 tickers at 2 ticks/second, the expected wait is `1 / (10 × 2 × 0.001) = 50 seconds` — frequent enough that something interesting happens during a demo, rare enough that the price series is not pure noise.

The shock multiplies the price directly rather than feeding through GBM, so it is a genuine discontinuity — a gap, which is what real news does to a stock.

---

## 5. Unknown tickers

Any symbol passing the API-level pattern `^[A-Z][A-Z.]{0,5}$` works, with no allowlist. The AI chat can add anything the user names, and it behaves plausibly.

```python
def _add_ticker_internal(self, ticker: str) -> None:
    if ticker in self._prices:
        return
    self._tickers.append(ticker)
    self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50.0, 300.0))
    self._params[ticker] = TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))
```

- **Price**: seeded from `SEED_PRICES`, else uniform in $50–$300 — the range where most large-cap US equities actually trade.
- **Parameters**: `TICKER_PARAMS`, else `DEFAULT_PARAMS` (`σ=0.25`, `μ=0.05`) — a mid-range large cap.
- **Correlation**: no sector membership, so `CROSS_GROUP_CORR` (0.3) against everything.

`dict(DEFAULT_PARAMS)` copies rather than sharing the module-level dict — without the copy, per-ticker parameter tuning would mutate the default for every unknown ticker at once.

This is a real advantage over the Massive path, where an unknown symbol simply never produces a price and sits at `—` forever (`MASSIVE_API.md` §9).

---

## 6. Code structure

Two classes with a clean split: **`GBMSimulator` is pure and synchronous; `SimulatorDataSource` handles async lifecycle and the cache.**

```
┌──────────────────────────────────────────────────────────┐
│ SimulatorDataSource(MarketDataSource)                    │
│   owns the asyncio task, writes to PriceCache            │
│   start / stop / add_ticker / remove_ticker / get_tickers│
│                        │                                 │
│                        ▼                                 │
│ GBMSimulator                                             │
│   pure math, no I/O, no async, no cache reference        │
│   step() -> {ticker: price}                              │
└──────────────────────────────────────────────────────────┘
                         │
                         ▼
                  seed_prices.py
              constants only, no logic
```

The separation pays off in testing: `GBMSimulator` needs no event loop, no cache, and no mocks. Statistical properties are asserted by calling `step()` in a loop.

### `GBMSimulator` — pure math

```python
class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR

    def __init__(self, tickers, dt=DEFAULT_DT, event_probability=0.001): ...

    def step(self) -> dict[str, float]: ...
    def add_ticker(self, ticker: str) -> None: ...
    def remove_ticker(self, ticker: str) -> None: ...
    def get_price(self, ticker: str) -> float | None: ...
    def get_tickers(self) -> list[str]: ...
```

State is three parallel dicts keyed by ticker (`_prices`, `_params`) plus `_tickers` as the **ordered** list that indexes into the Cholesky matrix. Order matters: row `i` of `_cholesky` corresponds to `_tickers[i]`, which is why add and remove must both rebuild.

`__init__` adds every ticker via `_add_ticker_internal` and rebuilds Cholesky **once** at the end, rather than rebuilding per ticker — `O(n³)` instead of `O(n⁴)` on startup.

### `SimulatorDataSource` — the async wrapper

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache, update_interval=0.5, event_probability=0.001): ...

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(tickers=tickers, event_probability=self._event_prob)
        # Seed the cache with initial prices so SSE has data immediately
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

Three deliberate choices:

**Seed the cache in `start()` before creating the task.** The first SSE event then carries real prices rather than an empty object, so the watchlist never renders as ten dashes on load.

**`add_ticker` seeds immediately.** The new ticker has a price on the very next SSE event, with no wait for the following step — the reason adding a ticker feels instant.

**The `try` is inside the loop, around the step.** An exception logs and the loop continues on the next interval. Wrapping the loop instead would let one bad tick kill the feed permanently. This is the one place defensive handling is warranted: the background task has no caller to propagate to, and a dead price feed is a dead app.

`stop()` cancels the task and awaits it, swallowing `CancelledError` — the normal shutdown path, not an error.

### Parameters

`seed_prices.py` holds constants only. Prices are realistic as of project creation; `σ` and `μ` are annualised.

| Ticker | Seed | σ | μ | Note |
|---|---|---|---|---|
| AAPL | $190 | 0.22 | 0.05 | |
| GOOGL | $175 | 0.25 | 0.05 | |
| MSFT | $420 | 0.20 | 0.05 | |
| AMZN | $185 | 0.28 | 0.05 | |
| TSLA | $250 | 0.50 | 0.03 | High volatility, decorrelated |
| NVDA | $800 | 0.40 | 0.08 | High volatility, strong drift |
| META | $500 | 0.30 | 0.05 | |
| JPM | $195 | 0.18 | 0.04 | Low volatility (bank) |
| V | $280 | 0.17 | 0.04 | Low volatility (payments) |
| NFLX | $600 | 0.35 | 0.05 | |
| *unknown* | $50–300 | 0.25 | 0.05 | `DEFAULT_PARAMS` |

The σ spread is what makes the watchlist readable at a glance: V and JPM barely move while NVDA and TSLA jump, so the grid has visible texture instead of ten tickers twitching identically.

---

## 7. Behaviour over time

There is **no mean reversion and no session boundary.** Prices random-walk from their seed for as long as the container runs. Over a demo — minutes to hours — drift is small and the series looks like a trading day. Over a week of uptime, a ticker may wander far from its seed. That is correct GBM behaviour and not worth correcting: the state is in memory only, so a restart returns everything to the seed prices.

That in turn is why the rolling price history is not persisted (`MARKET_INTERFACE.md` §3). A restart legitimately resets the world.

---

## 8. Testing

Existing coverage is **73 tests passing at 91%** across the market module (`simulator.py` itself is at 98%). `GBMSimulator` is pure, so its tests are fast and deterministic under a seeded RNG.

**Deterministic tests** — seed both RNGs, since the simulator uses `numpy.random` for the normal draws and the stdlib `random` for events:

```python
def test_step_is_reproducible():
    np.random.seed(42)
    random.seed(42)
    sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
    first = sim.step()

    np.random.seed(42)
    random.seed(42)
    sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
    assert sim.step() == first
```

**Structural properties:**

- Prices stay strictly positive over many thousands of steps.
- `step()` returns exactly the current ticker set.
- Add/remove keeps `_tickers`, `_prices`, and `_params` consistent, and the Cholesky shape matches `len(_tickers)`.
- Unknown tickers seed within $50–$300 and get `DEFAULT_PARAMS`.
- `remove_ticker` on an untracked symbol is a no-op, not an error.

**Statistical properties** — over enough steps, with a tolerance:

```python
def test_realised_volatility_is_close_to_sigma():
    sim = GBMSimulator(tickers=["AAPL"], event_probability=0.0)
    prices = [sim.get_price("AAPL")]
    for _ in range(20_000):
        prices.append(sim.step()["AAPL"])

    log_returns = np.diff(np.log(prices))
    realised = log_returns.std() / np.sqrt(GBMSimulator.DEFAULT_DT)
    assert 0.15 < realised < 0.35      # nominal sigma is 0.22
```

Disable events (`event_probability=0.0`) for statistical tests — a 5% jump is a massive outlier at this dt and will dominate the sample variance. Keep tolerances wide; these are sampling estimates, and a tight bound produces a test that fails a few times a year for no reason.

**Correlation:**

```python
def test_tech_tickers_are_positively_correlated():
    sim = GBMSimulator(tickers=["AAPL", "MSFT"], event_probability=0.0)
    a, m = [], []
    for _ in range(10_000):
        p = sim.step()
        a.append(p["AAPL"])
        m.append(p["MSFT"])

    rho = np.corrcoef(np.diff(np.log(a)), np.diff(np.log(m)))[0, 1]
    assert rho > 0.4        # nominal 0.6
```

**Cholesky positive-definiteness** — run after any change to the correlation constants:

```python
def test_correlation_matrix_stays_positive_definite():
    tickers = list(SEED_PRICES) + [f"UNK{i}" for i in range(40)]
    GBMSimulator(tickers=tickers)      # raises LinAlgError if not PD
```

**`SimulatorDataSource`** needs an event loop and a real `PriceCache`, but no mocks:

- `start()` populates the cache before returning.
- The cache updates after roughly one interval.
- `add_ticker` seeds a price immediately.
- `remove_ticker` clears the ticker from the cache.
- `stop()` is idempotent and halts writes.

```bash
cd backend
uv run pytest tests/market/
uv run pytest --cov=app --cov-report=term-missing
```

`backend/market_data_demo.py` is a Rich terminal demo of the live simulator — the fastest way to eyeball whether a parameter change still looks right.

---

## 9. Tuning guide

Everything worth adjusting, and what it costs:

| Want | Change | Watch for |
|---|---|---|
| More visible price motion | Raise `σ` in `TICKER_PARAMS` | Above ~0.8 it stops looking like equity |
| Faster or slower updates | `update_interval` on `SimulatorDataSource` | `DEFAULT_DT` is derived from 0.5s; change both together or annualised σ shifts |
| More frequent drama | Raise `event_probability` | Above ~0.005 the series becomes jumps, not prices |
| Bigger shocks | Widen `random.uniform(0.02, 0.05)` | Beyond ~10% the P&L chart loses all detail |
| Different sector behaviour | Edit `CORRELATION_GROUPS` and the coefficients | Re-run the positive-definiteness test in §8 |
| Different starting prices | `SEED_PRICES` | Unlisted tickers still land in $50–$300 |
| A trending market | Raise `μ` | `μ` is annualised; even 0.5 is barely visible over a demo |

The `DEFAULT_DT` coupling is the one that catches people. `DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR` hard-codes the 500ms tick. Passing `update_interval=0.1` to `SimulatorDataSource` without also passing a matching `dt` to `GBMSimulator` makes the simulation run five times faster in model time — annualised volatility silently becomes 5× what `TICKER_PARAMS` claims.

---

## 10. Summary

| Concern | Approach |
|---|---|
| Price model | Geometric Brownian Motion, per-ticker `μ` and `σ` |
| Positivity | Guaranteed by the multiplicative `exp` form — no clamping |
| Time step | `0.5s / (252 × 6.5 × 3600)` ≈ `8.479e-8` of a trading year |
| Correlation | Cholesky factor of a sector-block matrix, rebuilt on add/remove |
| Sectors | tech 0.6, finance 0.5, cross-sector and unknown 0.3, TSLA 0.3 |
| Drama | 0.1% chance per ticker per tick of a 2–5% jump — about one per 50s |
| Unknown tickers | Random $50–$300 seed, `DEFAULT_PARAMS`, cross-sector correlation |
| Structure | Pure `GBMSimulator` + async `SimulatorDataSource`, constants in `seed_prices.py` |
| Persistence | None — a restart returns to seed prices |
| Failure handling | Per-step `try` inside the loop; the feed never dies from one bad tick |
