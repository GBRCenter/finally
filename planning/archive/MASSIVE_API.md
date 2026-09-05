# MASSIVE_API.md — Massive (formerly Polygon.io) REST API

Reference for retrieving real-time and end-of-day prices for multiple tickers.

**Verified against `massive` Python SDK `2.2.0`** (installed in `backend/.venv`) and the official docs at <https://massive.com/docs>. Every signature, field name, and unit below was checked against the installed package source or the live documentation — not from memory.

> No `MASSIVE_API_KEY` was available in this repo when this document was written, so responses could not be exercised against the live service. Field shapes come from the SDK's `from_dict` parsers and the published response schemas, which is authoritative for how the client will deserialize. The verification script in §10 closes the loop once a key exists.

---

## 1. Orientation

Polygon.io rebranded to **Massive** in 2026. The API surface, the API keys, and the endpoint paths are unchanged; the hostname and the Python package are new.

| | Value |
|---|---|
| Base URL | `https://api.massive.com` |
| Python SDK | `massive` (PyPI), currently `2.2.0` |
| Repository | <https://github.com/massive-com/client-python> |
| Auth | `Authorization: Bearer <API_KEY>` header |
| Env var read by the SDK | `MASSIVE_API_KEY` |

The SDK reads the same environment variable name this project already uses, so `RESTClient()` with no arguments works when `MASSIVE_API_KEY` is exported. FinAlly passes the key explicitly instead, because the factory has already read and validated it.

### Install

```bash
uv add massive
```

### Authentication

The SDK sets the header for you (`massive/rest/base.py`):

```python
self.headers = {
    "Authorization": "Bearer " + self.API_KEY,
    "Accept-Encoding": "gzip",
    "User-Agent": f"Massive.com PythonClient/{version_number}",
}
```

Constructing a client with no key and no env var raises `massive.exceptions.AuthError` immediately — it does not wait for the first request.

```python
from massive import RESTClient

client = RESTClient(api_key="YOUR_KEY")   # or RESTClient() to read MASSIVE_API_KEY
```

Full constructor defaults, from the installed SDK:

```python
RESTClient(
    api_key: str | None = None,
    connect_timeout: float = 10.0,
    read_timeout: float = 10.0,
    num_pools: int = 10,
    retries: int = 3,                     # urllib3 Retry on 413/429/499/500/502/503...
    base: str = "https://api.massive.com",
    pagination: bool = True,
    verbose: bool = False,
    trace: bool = False,
    custom_json: Any | None = None,
)
```

Two consequences worth knowing:

- **`RESTClient` is synchronous.** It uses `urllib3.PoolManager`. Calling it from an `async def` blocks the event loop, which in this app means visibly stuttering prices on the SSE stream. Always wrap it in `asyncio.to_thread`.
- **It retries 429 internally** (3 attempts, honouring `Retry-After`). A poll that hits the rate limit therefore blocks its worker thread rather than failing fast.

---

## 2. Plans and rate limits

| Plan | Requests/min | Data freshness |
|---|---|---|
| Basic (free) | **5** | End-of-day, and 15-minute-delayed intraday |
| Paid (Starter and above) | Unlimited | Real-time (15-min delayed on Starter) |

This single number drives the whole polling design: **5 requests/minute means one request every 12 seconds at best.** FinAlly polls every 15 seconds by default, which leaves headroom and stays under the limit even if a poll overruns.

The corollary is that per-ticker endpoints are unusable on the free tier — 10 watchlist tickers via `get_last_trade` would be 10 requests per cycle, blowing the budget in one poll. **The design must fetch all tickers in a single request**, which is what §3 is about.

---

## 3. Real-time prices for multiple tickers

### 3.1 Full Market Snapshot (v2) — the primary endpoint

`GET /v2/snapshot/locale/us/markets/stocks/tickers`

One request returns the current state of every ticker you name. This is the endpoint FinAlly uses.

| Query param | Meaning |
|---|---|
| `tickers` | Case-insensitive comma-separated list. Omit to get the entire US market. |
| `include_otc` | Include OTC securities. Default `false`. |

SDK signature:

```python
client.get_snapshot_all(
    market_type: str | SnapshotMarketType,
    tickers: str | list[str] | None = None,
    include_otc: bool | None = False,
    params: dict | None = None,
    raw: bool = False,
) -> list[TickerSnapshot]
```

The SDK joins a list into a comma-separated string for you (`",".join(tickers)`), so passing a `list[str]` is correct and idiomatic.

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key="YOUR_KEY")

snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,
    tickers=["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"],
)

for snap in snapshots:
    print(snap.ticker, snap.last_trade.price, snap.todays_change_percent)
```

**Response shape** (`TickerSnapshot`, from `massive/rest/models/snapshot.py`):

| Attribute | JSON key | Type | Notes |
|---|---|---|---|
| `ticker` | `ticker` | `str` | |
| `todays_change` | `todaysChange` | `float` | Absolute change vs. prior close |
| `todays_change_percent` | `todaysChangePerc` | `float` | **Already in percent units** (`0.39` = 0.39%) |
| `updated` | `updated` | `int` | **Nanoseconds** |
| `day` | `day` | `Agg` | Today's bar so far |
| `prev_day` | `prevDay` | `Agg` | Previous session's bar |
| `min` | `min` | `MinuteSnapshot` | Most recent minute bar |
| `last_trade` | `lastTrade` | `LastTrade` | Most recent execution |
| `last_quote` | `lastQuote` | `LastQuote` | Most recent NBBO |
| `fair_market_value` | `fmv` | `float` | Business plans only; `None` otherwise |

`LastTrade` — note the attribute names, they do **not** match the JSON keys:

| Attribute | JSON key | Units |
|---|---|---|
| `price` | `p` | dollars |
| `size` | `s` | shares |
| `sip_timestamp` | `t` | **nanoseconds** |
| `exchange` | `x` | exchange ID |
| `conditions` | `c` | `list[int]` |
| `id` | `i` | trade ID |
| `ticker` | `T` | usually `None` inside a snapshot |

`Agg` (used for `day` and `prev_day`): `open`/`o`, `high`/`h`, `low`/`l`, `close`/`c`, `volume`/`v`, `vwap`/`vw`, `timestamp`/`t`, `transactions`/`n`.

### 3.2 Unified Snapshot (v3) — the alternative

`GET /v3/snapshot`

Multi-asset-class, paginated, and — the reason it is worth mentioning — it **reports unknown tickers explicitly** instead of silently omitting them.

```python
snaps = client.list_universal_snapshots(
    type="stocks",
    ticker_any_of=["AAPL", "NVDA", "NOTAREALTICKER"],
    limit=250,
)

for s in snaps:
    if s.error:
        print(f"{s.ticker}: {s.error} — {s.message}")   # e.g. NOT_FOUND
    else:
        print(s.ticker, s.session.close, s.last_trade.price)
```

- `ticker_any_of` accepts **up to 250** tickers.
- `limit` defaults to 10 and maxes at 250 — **leave it at the default and you will silently get only 10 results.** Always set it explicitly.
- Returns an *iterator* and auto-paginates (`pagination=True`), so a careless call can fan out into many billed requests. With `ticker_any_of` bounded at 250 and `limit=250` there is exactly one page.

FinAlly stays on v2 because a 10-ticker watchlist never approaches the 250 limit, v2 is a single non-paginated request, and the `error` field is of marginal value when the simulator is the default path anyway. v3 is the right upgrade if per-ticker validation feedback is ever wanted.

### 3.3 Single ticker

Useful for a one-off lookup; unusable as a polling strategy on the free tier.

```python
snap  = client.get_snapshot_ticker(SnapshotMarketType.STOCKS, "AAPL")
trade = client.get_last_trade("AAPL")     # LastTrade: .price, .size, .sip_timestamp (ns)
quote = client.get_last_quote("AAPL")     # LastQuote: .bid_price, .ask_price, ...
```

---

## 4. End-of-day prices

### 4.1 Previous close — per ticker

`GET /v2/aggs/ticker/{ticker}/prev`

```python
prev = client.get_previous_close_agg("AAPL", adjusted=True)
print(prev.ticker, prev.open, prev.high, prev.low, prev.close, prev.volume, prev.vwap)
```

`PreviousCloseAgg` fields: `ticker`, `open`, `high`, `low`, `close`, `volume`, `vwap`, `timestamp` (**milliseconds**, start of the aggregate window).

Works on the free tier. One request per ticker, so 10 tickers = 10 requests = two minutes of free-tier budget.

### 4.2 Daily market summary — the whole market in one request

`GET /v2/aggs/grouped/locale/us/market/stocks/{date}`

The efficient way to get EOD for many tickers: **one request returns every US ticker for that date.**

```python
from datetime import date

bars = client.get_grouped_daily_aggs(date="2026-08-28", adjusted=True)

wanted = {"AAPL", "GOOGL", "MSFT"}
closes = {b.ticker: b.close for b in bars if b.ticker in wanted}
print(closes)
```

`GroupedDailyAgg` adds a `ticker` attribute (JSON key `T`) to the standard `Agg` fields. `timestamp`/`t` is **milliseconds**, marking the *end* of the aggregate window.

Caveats: the date must be a **trading day** — a weekend or holiday returns an empty result set, not an error. And the response covers the entire market (thousands of rows), so filter client-side.

### 4.3 Daily open/close for one ticker on one date

`GET /v1/open-close/{ticker}/{date}`

```python
oc = client.get_daily_open_close_agg("AAPL", date="2026-08-28", adjusted=True)
print(oc.open, oc.close, oc.pre_market, oc.after_hours, oc.status)
```

`DailyOpenCloseAgg` is the one model that carries pre-market and after-hours prints. Note it uses `symbol` (not `ticker`) and `from_` (not `from`, which is a Python keyword).

---

## 5. Historical bars — for charts and backfill

`GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`

```python
# 1-minute bars for one session
bars = client.get_aggs(
    ticker="AAPL",
    multiplier=1,
    timespan="minute",         # second|minute|hour|day|week|month|quarter|year
    from_="2026-08-28",        # YYYY-MM-DD, date, datetime, or Unix ms
    to="2026-08-28",
    adjusted=True,
    sort="asc",
    limit=50000,
)

for b in bars:
    print(b.timestamp, b.open, b.high, b.low, b.close, b.volume)
```

`get_aggs` returns a **list** and is capped at 50,000 bars. `list_aggs` takes the same arguments but returns an **auto-paginating iterator** — convenient for long ranges, and a way to accidentally issue many billed requests. Prefer `get_aggs` with an explicit range unless you genuinely need more than 50k bars.

`Agg.timestamp` is **milliseconds**, marking the start of the window.

Relevance to FinAlly: this is the only way to seed a chart with real history under Massive. The rolling in-memory history in §6 of `PLAN.md` covers the simulator; a Massive-backed deployment could optionally backfill `GET /api/prices/{ticker}/history` from 1-minute aggregates instead. That is out of scope today, and noted here so the option is not rediscovered later.

---

## 6. Market status

Worth calling to explain a frozen feed to the user rather than leaving them guessing.

```python
status = client.get_market_status()
print(status.market)            # "open" | "closed" | "extended-hours"
print(status.exchanges)
print(status.after_hours, status.early_hours)
```

`client.get_market_holidays()` returns upcoming closures and early closes.

---

## 7. Timestamp units — the trap

Massive uses **three different time units across endpoints**, and the SDK passes them through unchanged. This is the single easiest thing to get wrong.

| Source | Attribute | Unit | To Unix seconds |
|---|---|---|---|
| Snapshot `lastTrade` | `sip_timestamp` | **nanoseconds** | `/ 1_000_000_000` |
| Snapshot `lastQuote` | `sip_timestamp` | **nanoseconds** | `/ 1_000_000_000` |
| Snapshot top level | `updated` | **nanoseconds** | `/ 1_000_000_000` |
| Snapshot `min` | `timestamp` | **milliseconds** | `/ 1_000` |
| Aggregates (`Agg`, `PreviousCloseAgg`, grouped) | `timestamp` | **milliseconds** | `/ 1_000` |

FinAlly's `PriceUpdate.timestamp` is **Unix epoch seconds as a float** (§7 of `PLAN.md`), so every value from this API needs converting, and the divisor depends on which endpoint it came from.

```python
NANOS_PER_SECOND = 1_000_000_000
MILLIS_PER_SECOND = 1_000

ts_seconds = snap.last_trade.sip_timestamp / NANOS_PER_SECOND   # snapshot
ts_seconds = agg.timestamp / MILLIS_PER_SECOND                  # aggregates
```

### Attribute names never match JSON keys

The wire format is single-letter (`p`, `s`, `t`, `x`); the SDK's `from_dict` maps those to readable attributes. You must use the **attribute** names. Reading `snap.last_trade.t` or `snap.last_trade.timestamp` raises `AttributeError`, because `@modelclass` builds a plain dataclass with no `__getattr__` fallback:

```python
# massive/rest/models/trades.py
@staticmethod
def from_dict(d):
    return LastTrade(
        d.get("T"), d.get("f"), d.get("q"), d.get("t"),   # "t" -> sip_timestamp
        d.get("y"), d.get("c"), d.get("e"), d.get("i"),
        d.get("p"),                                       # "p" -> price
        d.get("r"), d.get("s"), d.get("x"), d.get("z"),
    )
```

---

## 8. Two defects confirmed in `backend/app/market/massive_client.py`

Both were reproduced against the installed SDK, not inferred. They are recorded here because this document is the reference the fix should be written from; the fix itself belongs to whoever next touches that module.

### 8.1 `last_trade.timestamp` does not exist — the Massive path returns no prices at all

`_poll_once` reads:

```python
price = snap.last_trade.price
timestamp = snap.last_trade.timestamp / 1000.0    # AttributeError
```

Reproduction, using a payload shaped exactly as the v2 snapshot documentation specifies:

```python
from massive.rest.models.snapshot import TickerSnapshot

snap = TickerSnapshot.from_dict({
    "ticker": "AAPL",
    "lastTrade": {"p": 190.52, "s": 100, "t": 1755873791482000000, "x": 4},
})

snap.last_trade.price          # 190.52
snap.last_trade.sip_timestamp  # 1755873791482000000
snap.last_trade.timestamp      # AttributeError: 'LastTrade' object has no attribute 'timestamp'
```

The loop wraps each snapshot in `except (AttributeError, TypeError)` and merely logs a warning, so the exception is swallowed **once per ticker, on every poll**. The cache is never written. The observable symptom is not a crash: it is a watchlist where every ticker shows `—` forever, with `Skipping snapshot for AAPL` in the logs.

The correct attribute is `sip_timestamp`.

### 8.2 The unit divisor is wrong by a factor of 10⁶

Even with the attribute corrected, `/ 1000.0` treats nanoseconds as milliseconds. `1755873791482000000 / 1000` is ≈ 1.76 × 10¹⁵ seconds — roughly 55 million years in the future. Charts keyed on that timestamp would be unusable. The divisor must be `1_000_000_000`.

### 8.3 Why 94% test coverage did not catch either defect

`massive_client.py` is 94% covered and all 73 tests pass. The tests nonetheless assert the buggy behaviour, because they build snapshots from `MagicMock` (`backend/tests/market/test_massive.py`):

```python
def _make_snapshot(ticker: str, price: float, timestamp_ms: int) -> MagicMock:
    snap = MagicMock()
    snap.last_trade = MagicMock()
    snap.last_trade.price = price
    snap.last_trade.timestamp = timestamp_ms      # attribute the real model does not have
    return snap
```

A `MagicMock` answers to any attribute name, so `snap.last_trade.timestamp` resolves happily in the test and raises `AttributeError` in production. `test_timestamp_conversion` then locks in the wrong unit as well:

```python
assert update.timestamp == 1707580800.0    # asserts milliseconds -> seconds
```

The lesson generalises: **mocking a third-party model tests your assumptions about the library, not the library.** Parsing tests must go through the real `TickerSnapshot.from_dict` with a documented payload, as in §10. That form of test needs no network and would have failed on the first run.

### Corrected parse

```python
NANOS_PER_SECOND = 1_000_000_000

for snap in snapshots:
    trade = snap.last_trade
    if trade is None or trade.price is None:
        continue                      # no print yet today; leave the ticker showing "—"
    self._cache.update(
        ticker=snap.ticker,
        price=trade.price,
        timestamp=(
            trade.sip_timestamp / NANOS_PER_SECOND
            if trade.sip_timestamp
            else time.time()
        ),
    )
```

Guarding on `is None` rather than catching `AttributeError` is what makes the difference: a genuinely absent field is a normal condition to handle, whereas a misspelled attribute is a bug that should be loud. The existing blanket `except AttributeError` is precisely what hid this one.

---

## 9. Errors and operational behaviour

The SDK raises only two exception types (`massive/exceptions.py`):

| Exception | Cause |
|---|---|
| `AuthError` | Empty or missing API key at construction time |
| `BadResponse` | Any non-200 response that survived the retry policy |

`urllib3` raises its own errors for connection failures and timeouts. A poll loop should therefore catch broadly and keep going, since a failed poll is recoverable on the next cycle:

```python
from massive.exceptions import AuthError, BadResponse

try:
    snapshots = await asyncio.to_thread(self._fetch_snapshots)
except AuthError:
    logger.error("Massive API key rejected — falling back is not automatic")
    raise                       # unrecoverable: do not retry on a loop
except BadResponse as e:
    logger.warning("Massive returned an error response: %s", e)
    return                      # transient: retry next interval
except Exception:
    logger.exception("Massive poll failed")
    return
```

### Behaviours to surface in the README

These are properties of the data source, not bugs, and users will otherwise report them as bugs:

- **Unknown symbols vanish silently.** The v2 snapshot omits tickers it does not recognise; there is no error entry. The ticker sits in the watchlist showing `—` indefinitely. (v3 would report `NOT_FOUND` — see §3.2.)
- **Prices freeze outside market hours.** Overnight, at weekends, and on holidays the snapshot returns the last trade of the previous session. The UI looks broken but is correct. This is the main reason the simulator is the default.
- **Free-tier data is 15 minutes delayed**, so prices will not match any other quote source the user has open.
- **Snapshot data is cleared at midnight ET** and repopulates from about 4am ET. Between those times `last_trade` may be absent entirely — which is exactly the `None` case §8 guards.

---

## 10. Verification script

Run this once a real `MASSIVE_API_KEY` is available. It confirms auth, the multi-ticker snapshot, unit conversion, and the EOD path in one pass.

```python
# backend/scripts/verify_massive.py
"""Smoke-test the Massive REST API against a live key."""

import os
from datetime import UTC, datetime

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

NANOS_PER_SECOND = 1_000_000_000
TICKERS = ["AAPL", "GOOGL", "MSFT", "NVDA", "TSLA"]


def main() -> None:
    key = os.environ["MASSIVE_API_KEY"]
    client = RESTClient(api_key=key)

    status = client.get_market_status()
    print(f"market: {status.market}")

    snapshots = client.get_snapshot_all(SnapshotMarketType.STOCKS, TICKERS)
    print(f"requested {len(TICKERS)}, received {len(snapshots)}")

    for snap in snapshots:
        trade = snap.last_trade
        if trade is None or trade.price is None:
            print(f"{snap.ticker}: no trade data")
            continue
        seconds = trade.sip_timestamp / NANOS_PER_SECOND
        when = datetime.fromtimestamp(seconds, UTC)
        print(f"{snap.ticker}: ${trade.price:.2f} at {when:%Y-%m-%d %H:%M:%S} UTC")

    missing = set(TICKERS) - {s.ticker for s in snapshots}
    if missing:
        print(f"absent from response (unknown or untraded): {sorted(missing)}")

    prev = client.get_previous_close_agg("AAPL")
    print(f"AAPL previous close: ${prev.close:.2f}")


if __name__ == "__main__":
    main()
```

```bash
uv run python scripts/verify_massive.py
```

Expected: a market status, five priced tickers with timestamps in the recent past (not 55 million years hence), and a previous close. Timestamps far in the future mean the unit divisor is wrong; `AttributeError` means §8.1 has regressed.

---

## 11. Summary — what FinAlly uses

| Need | Endpoint | SDK call | Cost |
|---|---|---|---|
| Live prices, all watched tickers | `/v2/snapshot/.../tickers` | `get_snapshot_all` | 1 request per poll |
| EOD close, one ticker | `/v2/aggs/ticker/{t}/prev` | `get_previous_close_agg` | 1 request per ticker |
| EOD close, many tickers | `/v2/aggs/grouped/...` | `get_grouped_daily_aggs` | 1 request total |
| Chart backfill | `/v2/aggs/ticker/{t}/range/...` | `get_aggs` | 1 request per ticker |
| Explain a frozen feed | `/v1/marketstatus/now` | `get_market_status` | 1 request |

The polling design that follows from the 5 req/min free tier — one snapshot request covering the union of watchlist and held positions, every 15 seconds — is specified in `MARKET_INTERFACE.md`.

## Sources

- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot)
- [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot)
- [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
- [Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Request limits for Massive's RESTful APIs](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis)
- [massive-com/client-python](https://github.com/massive-com/client-python)
- Installed SDK source: `backend/.venv/lib/python3.13/site-packages/massive/` (v2.2.0)
