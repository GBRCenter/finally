# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area, backfilled from the server's rolling price history so the chart is populated immediately
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Review their trade history** — a compact blotter of executed trades beneath the positions table
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat
- **Reset the simulation** — one button returns the account to a clean $10,000 with the default watchlist, so the app can be demoed repeatedly

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (bind-mounted to ./db)         │
│  Background tasks: market data + snapshots       │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, bind-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Bind mount over named volume | `./db/finally.db` is visible on the host — students can inspect it with any SQLite browser, or delete it to reset |
| Single Docker container | Students run one command; no docker-compose, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |
| Recharts as the only chart library | Line charts, sparklines, and the treemap heatmap all come from one dependency — no second charting mental model |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── app/
│       ├── market/           # Market data (complete — see MARKET_DATA_SUMMARY.md)
│       └── db/               # Schema definitions, seed data, connection handling
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests
├── db/                       # Bind mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; db/*.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/app/db/`** contains schema SQL definitions, seed logic, and connection handling. It is application code and imports as `app.db`. The backend lazily initializes the database on first request — creating tables and seeding default data if the SQLite file doesn't exist or is empty. (Note the deliberate distinction from the root `db/`, which holds no code.)
- **`db/`** at the top level is the runtime bind mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests. Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands. These are the only supported launch path — there is deliberately no `docker-compose.yml`, so there is only one thing to keep in sync.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (see §9, LLM Mock Mode)

### How `.env` Is Loaded

Two distinct mechanisms — do not confuse them:

- **Local development** (running `uv run uvicorn ...` directly): the backend reads `.env` from the project root via `python-dotenv`. There is no container involved.
- **Docker**: `docker run --env-file .env ...` injects the variables as real environment variables. The `.env` file is *not* mounted into the container and does not exist inside it.

Both paths end with the same `os.environ` contents, so backend code only ever reads `os.environ`.

---

## 6. Market Data

> Status: **complete**. Implemented in `backend/app/market/`. See `planning/MARKET_DATA_SUMMARY.md` for the module map and `backend/CLAUDE.md` for the API. The subsections below record the contract that downstream code depends on, including three additions still to be built (marked **TODO**).

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface (`MarketDataSource`). The backend selects which to use via `create_market_data_source(cache)`, based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers via Cholesky decomposition (tech 0.6, finance 0.5, cross-sector 0.3)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (AAPL $190, GOOGL $175, etc.)
- **Unknown tickers are supported**: a ticker with no entry in `SEED_PRICES` gets a random start in $50–$300, `DEFAULT_PARAMS` for drift/volatility, and cross-sector correlation. Adding a ticker seeds the cache immediately, so it has a price on the very next SSE event.
- Runs as an in-process background task — no external dependencies

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of all tracked tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds. Paid tiers: 2-15 seconds.
- Parses REST response into the same `PriceUpdate` format as the simulator
- **Known limitations to surface in the README**: an unknown or invalid symbol simply never produces a price, so the ticker sits in the watchlist showing `—`; and outside regular trading hours the API returns the last close, so prices appear frozen on evenings and weekends. The simulator is the default precisely because it always looks alive.

### Which Tickers Are Tracked

**The tracked ticker set is `watchlist ∪ {tickers with a non-zero position}`.**

This matters because the two sets diverge: a user can buy TSLA and then remove TSLA from the watchlist, and the position still needs a live price for valuation, P&L, the heatmap, and snapshots. `SimulatorDataSource.remove_ticker()` deletes the ticker from the cache, so calling it for a held ticker would silently freeze that position's value.

The rule, therefore:

- `POST /api/watchlist` → always `await source.add_ticker(t)`
- `DELETE /api/watchlist/{t}` → `await source.remove_ticker(t)` **only if no position in `t` is held**. Otherwise the ticker leaves the watchlist UI but stays in the feed.
- Buying a ticker that is not tracked → `await source.add_ticker(t)` as part of trade execution
- Selling a position to zero → if `t` is not in the watchlist, `await source.remove_ticker(t)`

### Ticker Validation

Applied at the API boundary (`POST /api/watchlist`, `POST /api/portfolio/trade`, and every LLM-proposed action), so the rest of the system only ever sees canonical symbols:

- Normalize: `ticker.strip().upper()`
- Validate against `^[A-Z][A-Z.]{0,5}$` — reject anything else with `400`
- No allowlist. Any symbol matching the pattern is accepted; the simulator invents plausible behavior for it, and under Massive an unknown symbol shows `—`. Rejecting unknown symbols would make the LLM's `watchlist_changes` feature feel broken.
- Tickers are stored uppercase everywhere. The `UNIQUE(user_id, ticker)` constraints would otherwise happily hold both `AAPL` and `aapl`.

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to `PriceCache`, an in-memory, thread-safe store
- The cache holds the latest price, previous price, and timestamp for each ticker, plus a monotonic `version` counter that increments on every update
- SSE streams, portfolio valuation, and trade execution all read from this cache
- This architecture supports future multi-user scenarios without changes to the data layer

### Rolling Price History (**TODO**)

`PriceCache` keeps, per ticker, a bounded `deque` of the last **600** `(timestamp, price)` points — about five minutes at the 500ms simulator cadence. This exists solely so the main chart is populated the instant a user clicks a ticker, rather than drawing itself from scratch over the following minute. It is served by `GET /api/prices/{ticker}/history`.

Memory cost is trivial (600 points × ~50 tickers × 16 bytes ≈ 500KB) and it is deliberately *not* persisted — restarting the app clears it, which is the honest behavior for a simulator with no real history.

### SSE Streaming

- Endpoint: `GET /api/stream/prices`, `Content-Type: text/event-stream`
- Long-lived SSE connection; client uses the native `EventSource` API
- The stream opens with `retry: 1000`, then pushes the **entire price cache** as a single JSON object whenever `PriceCache.version` changes, checked every 500ms. Payload shape is a map keyed by ticker:

```
retry: 1000

data: {"AAPL": {"ticker": "AAPL", "price": 190.52, "previous_price": 190.48, "timestamp": 1755873791.482, "change": 0.04, "change_percent": 0.021, "direction": "up"}, "GOOGL": {...}}
```

- **`timestamp` is Unix epoch seconds as a float** (not ISO), and **`change_percent` is already in percent units** (`0.021` means 0.021%, not 2.1%). The frontend must not multiply by 100 again. This is `PriceUpdate.to_dict()` and it is frozen — treat it as the contract.
- Because the first loop iteration always sees a version change, a newly connected client receives a **full snapshot immediately**, including after a reconnect. No separate snapshot endpoint or event type is needed.
- **TODO — keepalive**: when the version has not changed for 15 seconds, emit an SSE comment line (`: ping\n\n`). Without it, a Massive-backed feed (15s polls) sends no bytes between polls, which idle-timeouts through proxies and leaves the frontend unable to distinguish "quiet market" from "connection dead". The connection indicator depends on this.
- Client reconnection is automatic (`EventSource` built-in retry, 1s as directed by the stream)

---

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup (or first request). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh containers start with a clean, seeded database automatically

### Access Pattern

A background snapshot task, long-lived SSE generators, and request handlers all share one process and one event loop. Synchronous `sqlite3` calls inside an `async def` handler block that loop — with an SSE stream attached, that shows up as visibly stuttering prices. So:

- Enable **WAL mode** at initialization (`PRAGMA journal_mode=WAL`) plus `PRAGMA foreign_keys=ON` and a busy timeout
- Use short-lived connections per operation with `check_same_thread=False`
- Dispatch DB work off the event loop: either write DB functions as plain `def` route handlers (FastAPI runs them in its threadpool automatically) or wrap calls in `asyncio.to_thread`. Never call `sqlite3` directly from an `async def` handler.

### Timestamps

Two different representations, deliberately, and they must not be mixed up:

- **Database columns** are ISO 8601 TEXT, **timezone-aware UTC**: `datetime.now(UTC).isoformat()` → `"2026-08-22T14:03:11.482000+00:00"`. Naive local timestamps break chart axes and cross-restart ordering.
- **Price data** (`PriceUpdate.timestamp`, SSE payloads) is a Unix epoch float, because that is what the market layer already emits and what charting libraries want.

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration. Foreign keys between `user_id` and `users_profile.id` are deliberately **not** declared — with one hardcoded user they add ceremony without protection.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp, UTC)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT (uppercase)
- `added_at` TEXT (ISO timestamp, UTC)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT (uppercase)
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp, UTC)
- UNIQUE constraint on `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT (uppercase)
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp, UTC)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp, UTC)

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — trades executed, watchlist changes made; null for user messages)
- `created_at` TEXT (ISO timestamp, UTC)

### Snapshot Task Rules

- Cadence: every 30 seconds, from a single background task started at app startup
- **Snapshots are not written on trade execution.** A trade does not change total portfolio value — cash out, equal position value in — so a per-trade snapshot only adds a duplicate point at a moment when the value is, by construction, unchanged. The 30-second cadence tells the whole story.
- **Skip the write entirely if any held ticker has no cached price.** Right after startup the cache may be empty, and under Massive the first poll can be 15 seconds out. Writing then puts a bogus first point on the P&L chart at every restart.
- Growth is ~2,880 rows/day, unbounded and harmless at demo scale. `GET /api/portfolio/history` caps and orders results rather than the writer pruning.

### Portfolio Math — Canonical Formulas

Backend valuation, the header, the snapshot task, and the LLM context block all compute these. They are written down once, here, so they cannot drift:

```
position_value  = quantity × current_price
positions_value = Σ position_value
total_value     = cash_balance + positions_value
unrealized_pnl  = quantity × (current_price − avg_cost)
pct_change      = (current_price − avg_cost) / avg_cost
weight          = position_value / total_value

buy:   avg_cost = (old_qty × old_avg_cost + qty × price) / (old_qty + qty)
       cash_balance −= qty × price
sell:  avg_cost unchanged
       cash_balance += qty × price
```

- **When a held ticker has no cached price, value it at `avg_cost`** (P&L reads as zero rather than as a crash).
- **Realized P&L is not tracked.** Selling at a profit simply moves value into cash and the position disappears from the table. The P&L chart on total portfolio value is the single source of performance truth. This is a deliberate simplification, not an oversight — do not add a `realized_pnl` column.
- **Round `cash_balance` to 2 decimals** after every trade, or the header eventually reads `9999.999999999998`.
- **After a sell, delete the position row if `quantity < 1e-9`.** Floating-point residue of `2.8e-16` shares renders as `0.00` but would otherwise keep the ticker in the positions table and in the tracked feed forever.

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

`POST /api/reset` restores exactly this state: truncate `positions`, `trades`, `portfolio_snapshots`, and `chat_messages`; set `cash_balance` to 10000.0; replace the watchlist with the ten defaults; and re-sync the market data source's tracked tickers to match.

---

## 8. API Contract

Every endpoint's exact request and response shape is specified here. This section is the contract between the Backend and Frontend agents — neither should invent field names. Field naming is `snake_case` throughout, matching the database and the existing `PriceUpdate.to_dict()`.

### Conventions

- **Errors**: every non-2xx response is `{"error": "human readable message"}`. Status codes: `400` invalid input or failed business rule (insufficient cash, insufficient shares, bad ticker), `404` unknown resource, `503` LLM unavailable. FastAPI's default `422` body is replaced by an exception handler so the shape is uniform.
- **Money** is a JSON number rounded to 2 decimals. **Quantities** are numbers with up to 6 decimals. **Percentages** in REST responses are fractions (`0.0221` = 2.21%) — note this differs from the SSE `change_percent` field, which is already in percent units and is frozen for backward compatibility with the shipped market module.
- All timestamps in REST responses are ISO 8601 UTC strings, matching the database.

### System

**`GET /api/health`** → `200`
```json
{"status": "ok", "market_source": "simulator", "llm_mock": false}
```

**`POST /api/reset`** → `200` — restores the seeded state described in §7.
```json
{"cash_balance": 10000.0, "watchlist": ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]}
```

### Market Data

**`GET /api/stream/prices`** → SSE. Payload shape is specified in §6 and is frozen.

**`GET /api/prices/{ticker}/history?limit=600`** → `200` — rolling in-memory history for the main chart. Empty `points` for an untracked ticker (not a 404 — the chart just draws nothing).
```json
{"ticker": "AAPL", "points": [{"timestamp": 1755873791.482, "price": 190.52}]}
```

### Portfolio

**`GET /api/portfolio`** → `200` — one call gives the frontend everything the header, positions table, and heatmap need. Prices and P&L are computed server-side using the §7 formulas so the frontend never joins two sources.
```json
{
  "cash_balance": 8055.90,
  "positions_value": 2003.60,
  "total_value": 10059.50,
  "unrealized_pnl": 59.50,
  "positions": [
    {
      "ticker": "AAPL",
      "quantity": 10.0,
      "avg_cost": 190.25,
      "current_price": 194.46,
      "market_value": 1944.60,
      "unrealized_pnl": 42.10,
      "pct_change": 0.0221,
      "weight": 0.1933
    }
  ]
}
```

**`POST /api/portfolio/trade`** — request:
```json
{"ticker": "AAPL", "side": "buy", "quantity": 10}
```
→ `200`:
```json
{
  "trade": {"id": "uuid", "ticker": "AAPL", "side": "buy", "quantity": 10.0, "price": 194.46, "executed_at": "2026-08-22T14:03:11.482000+00:00"},
  "cash_balance": 8055.90,
  "position": {"ticker": "AAPL", "quantity": 10.0, "avg_cost": 194.46}
}
```
`position` is `null` when a sell closes the position. Validation, all `400`:

| Condition | Message |
|---|---|
| `quantity <= 0`, NaN, or infinite | `Quantity must be a positive number` |
| `side` not `buy`/`sell` | `Side must be 'buy' or 'sell'` |
| Ticker fails the §6 pattern | `Invalid ticker symbol` |
| No cached price for the ticker | `No price available for TSLA yet` |
| Buy where `qty × price > cash_balance` | `Insufficient cash: need $1944.60, have $500.00` |
| Sell where `qty > held quantity` | `Insufficient shares: you hold 3 AAPL` |

No shorting, no margin. Fill price is the cache price read at request time.

**`GET /api/portfolio/history?limit=500&since=<iso>`** → `200` — for the P&L chart. Newest-last, capped at `limit` (default 500, max 2000); `since` is optional.
```json
{"snapshots": [{"total_value": 10000.0, "recorded_at": "2026-08-22T14:00:00+00:00"}]}
```

**`GET /api/trades?limit=50`** → `200` — for the blotter. Newest-first.
```json
{"trades": [{"id": "uuid", "ticker": "AAPL", "side": "buy", "quantity": 10.0, "price": 194.46, "executed_at": "..."}]}
```

### Watchlist

**`GET /api/watchlist`** → `200` — `price` and its siblings are `null` until the first tick for that ticker.
```json
{
  "tickers": [
    {"ticker": "AAPL", "price": 194.46, "previous_price": 194.40, "change": 0.06, "direction": "up", "added_at": "..."},
    {"ticker": "PYPL", "price": null, "previous_price": null, "change": null, "direction": null, "added_at": "..."}
  ]
}
```

**`POST /api/watchlist`** — request `{"ticker": "pypl"}` → `201 {"ticker": "PYPL", "added_at": "..."}`. Normalized and validated per §6. Adding a ticker already present is a no-op returning `200`, not an error.

**`DELETE /api/watchlist/{ticker}`** → `204`. `404` if not on the watchlist. Per §6, the ticker stays in the price feed if a position is held.

### Chat

**`POST /api/chat`** — request `{"message": "buy 10 apple"}`, max 2000 characters (`400` beyond that). See §9 for behavior. → `200`:
```json
{
  "id": "uuid",
  "message": "Bought 10 AAPL at $194.46. Your tech concentration is now 68% — consider diversifying.",
  "actions": {
    "trades": [
      {"ticker": "AAPL", "side": "buy", "quantity": 10, "status": "executed", "price": 194.46},
      {"ticker": "TSLA", "side": "sell", "quantity": 50, "status": "rejected", "error": "Insufficient shares: you hold 10 TSLA"}
    ],
    "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "executed"}]
  },
  "created_at": "..."
}
```
`actions` always has both arrays, possibly empty. Every entry carries `status` of `"executed"` or `"rejected"`, and rejected entries carry `error`.

**`GET /api/chat?limit=50`** → `200` — so the chat panel survives a page refresh. Oldest-first, the last `limit` messages.
```json
{"messages": [{"id": "uuid", "role": "user", "content": "buy 10 apple", "actions": null, "created_at": "..."}]}
```

**`DELETE /api/chat`** → `204` — clears conversation history only. Does not touch the portfolio.

---

## 9. LLM Integration

When writing code to make calls to LLMs, use the `cerebras` skill to call LiteLLM via OpenRouter against the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs interpret the result.

There is an `OPENROUTER_API_KEY` in the `.env` file in the project root.

> **Verify before building**: confirm that strict JSON-schema structured output actually works end-to-end through OpenRouter's Cerebras routing for this model. If it does not, fall back to prompt-enforced JSON plus the parse-failure path below — the rest of this section is unchanged either way.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value) using the §7 formulas
2. Loads the **last 20 messages** from `chat_messages` (oldest-first)
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output
5. Parses the structured JSON response
6. Validates and executes any trades or watchlist changes specified in the response
7. Stores the user message and the assistant message (with its `actions` JSON) in `chat_messages`
8. Returns the response shape specified in §8 (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator suffices)

Only one chat request may be in flight at a time; the frontend disables the input while waiting. Combined with the 2000-character message cap and the 20-message history window, this bounds both latency and OpenRouter spend — a retry loop on a failing endpoint would otherwise burn real credits.

### Structured Output Schema

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

**All three fields are required.** `trades` and `watchlist_changes` are `[]` when there is nothing to do. Required-with-empty-array is markedly more reliable with structured outputs than optional-and-absent, and it removes a null check from every consumer.

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

**Execution is best-effort and ordered.** Each action runs through exactly the same validation as a manual trade (§8). If the second of three trades fails, the first and third still execute. Every action is recorded in the response with `status` and, on failure, `error`.

**Failures are reported without a second LLM call.** The model has already written its `message` by the time validation runs, so it cannot narrate a failure it never saw. Instead:

- The backend appends a deterministic note to the stored assistant message, e.g. `\n\nNote: sell 50 TSLA was not executed — insufficient shares: you hold 10 TSLA.`
- The frontend additionally renders each action as an inline chip beside the message — green for executed, red with the error text for rejected.

These compose, cost nothing, and add no latency. A second round-trip feeding failures back to the model would be more "agentic" but doubles cost and latency for a message the user can already read.

### Malformed Response Handling

The endpoint must never return a 500 into the chat panel. On a response that fails to parse or fails schema validation: retry once, then return
```json
{"message": "I had trouble forming a response — please try again.", "actions": {"trades": [], "watchlist_changes": []}}
```
with `200`. If OpenRouter itself is unreachable or unauthorized, return `503` with the standard error envelope so the frontend can distinguish "the AI is down" from "the AI is confused".

### System Prompt Guidance

Prompt the LLM as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Use uppercase ticker symbols
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic responses without calling OpenRouter — no API key needed, free, instant, reproducible. **The mock's behavior is part of the contract**, because E2E tests assert on it. It is keyword-driven on the lowercased user message, first match wins:

| Trigger | `message` | `trades` | `watchlist_changes` |
|---|---|---|---|
| contains `buy` | `Mock: buying 1 share of AAPL.` | `[{"ticker":"AAPL","side":"buy","quantity":1}]` | `[]` |
| contains `sell` | `Mock: selling 1 share of AAPL.` | `[{"ticker":"AAPL","side":"sell","quantity":1}]` | `[]` |
| contains `watch` | `Mock: adding PYPL to your watchlist.` | `[]` | `[{"ticker":"PYPL","action":"add"}]` |
| anything else | `Mock: your portfolio is worth $X.` (X from live context) | `[]` | `[]` |

Mock responses flow through the identical execution and validation path as real ones, so a mocked `sell` with no position produces a genuine rejection — which is exactly what the E2E error-path test needs.

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), change, and a sparkline mini-chart accumulated from SSE since page load. Tickers with no price yet show `—`.
- **Main chart area** — larger chart for the currently selected ticker. Backfilled on selection from `GET /api/prices/{ticker}/history`, then extended live from the SSE stream. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap where each rectangle is a position, sized by `weight` and colored by `unrealized_pnl` (green = profit, red = loss)
- **P&L chart** — line chart of total portfolio value over time from `GET /api/portfolio/history`
- **Positions table** — ticker, quantity, avg cost, current price, unrealized P&L, % change — rendered directly from `GET /api/portfolio`, which already computes all of it
- **Trade blotter** — compact newest-first list of executed trades from `GET /api/trades`, beneath the positions table
- **Trade bar** — ticker field, quantity field (accepts fractional input), buy button, sell button. Market orders, instant fill. Server-side validation errors are shown inline verbatim — the messages in §8 are written to be user-facing.
- **AI chat panel** — docked/collapsible sidebar. Message input (2000-char cap, disabled while a request is in flight), scrolling history restored on load from `GET /api/chat`, loading indicator while waiting. Executed and rejected actions rendered as inline chips beside each assistant message.
- **Header** — portfolio total value (updating live), cash balance, connection status indicator, and a reset button (with a confirm step, since it is the one destructive action in the app)

### Data Flow

Two sources, and it is worth being precise about which owns what:

- **SSE** owns live prices. Every event carries the full cache, so the client replaces its price map wholesale — no merging logic, no missed-update reconciliation.
- **REST** owns everything else. Re-fetch `GET /api/portfolio` and `GET /api/trades` after any trade or chat response; poll `GET /api/portfolio/history` every 30 seconds to match the snapshot cadence.
- Portfolio *values* shown in the header can be recomputed client-side from the SSE price map between fetches, using the §7 formulas, so the total ticks along with prices rather than jumping every 30 seconds.

### Technical Notes

- Use `EventSource` for SSE to `/api/stream/prices`. Connection indicator: green on `onopen`, yellow on `onerror` (EventSource retries automatically), red after repeated failures or a `: ping` gap exceeding ~40 seconds.
- Remember the two SSE quirks from §6: `timestamp` is Unix epoch **seconds as a float** (multiply by 1000 for `Date`), and `change_percent` is **already a percentage**.
- **Recharts for every chart** — the main line chart, the sparklines, and the `<Treemap>` heatmap. Lightweight Charts has no treemap, so choosing it would force a second charting library for one component; at 10 tickers and 500ms updates Recharts is comfortably fast enough.
- Price flash effect: on receiving a new price, briefly apply a CSS class with a background-color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme
- Next.js static export means no server components, route handlers, middleware, or image optimization — this is a client-rendered SPA that Next happens to bundle. Set `output: 'export'` and `images: {unoptimized: true}`, and keep everything under a single route.

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm ci && npm run build (produces static export in out/)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync --frozen (install Python dependencies from lockfile)
  - Copy frontend build output into static/
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

### Route Mounting Order

FastAPI serves both the API and the static frontend on port 8000, so mounting order matters:

1. Mount all `/api/*` routers **first**
2. Mount static assets
3. Register a catch-all last that returns `index.html` for any unmatched path, so client-side routing and hard refreshes work

A `StaticFiles(html=True)` mount at `/` registered before the API routers will shadow every endpoint. This is the single most common way to break this architecture.

### Persistence

The SQLite database persists via a bind mount, so the file is visible and deletable on the host:

```bash
docker run -v "$(pwd)/db:/app/db" -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container; the backend writes `finally.db` there. On Windows the PowerShell scripts use `${PWD}` and quote the path, since spaces in the path are common.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` is passed)
- Runs the container with the bind mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT touch `db/` (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents.

All scripts are idempotent — safe to run multiple times. They are the only supported launch path; there is no `docker-compose.yml` to keep in sync.

### Optional Cloud Deployment

The container can deploy to AWS App Runner, Render, or any container platform, and a Terraform configuration may be provided in `deploy/` as a stretch goal.

**If you deploy it, put it behind authentication.** The app has no login by design, and `POST /api/chat` spends the deployer's OpenRouter credits and auto-executes trades on every call. Publicly reachable, that is an open API-key proxy. Use basic auth at the platform edge, an IP allowlist, or don't deploy. Locally this is a non-issue — the warning exists only because the plan invites deployment.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: complete — 73 tests, 84% coverage (see `MARKET_DATA_SUMMARY.md`). Extend with tests for the rolling price history and the SSE keepalive.
- Portfolio: trade execution, the §7 formulas, and every row of the §8 validation table — plus fractional-residue cleanup (sell-all leaves no row) and cash rounding
- Ticker tracking: removing a watchlist ticker with an open position keeps it in the feed; selling to zero off-watchlist removes it
- Snapshots: no snapshot written while a held ticker has no price; none written on trade execution
- LLM: structured output parsing, the retry-then-fallback path on malformed responses, best-effort partial execution, and the mock-mode table in §9
- API routes: status codes, the exact response shapes in §8, and the uniform error envelope

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- SSE payload handling — the map-shaped event, the float timestamp, and the already-percent `change_percent`
- Watchlist CRUD operations
- Chat message rendering, loading state, and executed/rejected action chips

### E2E Tests (in `test/`)

**Infrastructure**: Playwright runs **on the host** against the container started by `scripts/start_mac.sh` (`npx playwright test`, `baseURL: http://localhost:8000`). This tests exactly the artifact users run, with no orchestration to build or debug. A containerized runner can be added later for CI, but it is not the documented path.

**Environment**: the container runs with `LLM_MOCK=true`, making chat responses deterministic per the §9 table.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio and blotter update
- Sell shares: cash increases, position updates or disappears entirely
- Rejected trade: buying beyond available cash shows the inline error and changes nothing
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send `buy`, see the response and an executed-trade chip; send `sell` with no position, see a rejected chip with the error
- Chat persistence: reload the page, history is still there
- Reset: returns to $10k with the default watchlist and an empty positions table
- SSE resilience: disconnect and verify reconnection and the connection indicator

---

## 13. Decisions Log

A documentation review on 2026-08-22 raised 30 questions and gaps. Their resolutions are now written into the sections above rather than listed here; this log records what was decided, where it landed, and what remains open.

### Resolved and incorporated

| Issue | Decision | Where |
|---|---|---|
| Named volume vs. bind mount contradiction | Bind mount `./db:/app/db` | §3, §11 |
| Held ticker removed from watchlist loses its price | Tracked set = `watchlist ∪ positions` | §6 |
| Arbitrary ticker input undefined | Normalize + regex at the API boundary; no allowlist | §6 |
| Chat history written but unreadable | `GET /api/chat`, `DELETE /api/chat` | §8 |
| "LLM informs the user of the failure" was circular | Deterministic backend note + frontend chips, no second call | §9 |
| Multi-trade partial failure semantics | Best-effort and ordered, every action reports status | §9 |
| No price data on page load | `GET /api/portfolio` computes prices and P&L server-side | §8 |
| Valuation with a missing price | Fall back to `avg_cost`; skip the snapshot entirely | §7 |
| P&L formulas would drift across four call sites | Written once, canonically | §7 |
| Fractional-share residue | Delete the position below `1e-9` | §7 |
| Trade validation edges | Full table with user-facing messages | §8 |
| Timestamp representation | ISO UTC in the DB, Unix float in the price layer | §7 |
| `LLM_MOCK` behavior undefined but asserted on | Keyword table, part of the contract | §9 |
| Massive polling looks like a dead connection | `: ping` keepalive every 15s (**TODO**) | §6 |
| Unbounded prompt growth | Last 20 messages, 2000-char cap, one request in flight | §9 |
| Malformed LLM output | Retry once, then a canned 200; 503 only if the provider is down | §9 |
| Main chart had no data source | 600-point rolling history + `GET /api/prices/{ticker}/history` (**TODO**) | §6, §8 |
| Two candidate chart libraries | Recharts for all of it | §3, §10 |
| No way to reset the demo | `POST /api/reset` | §7, §8 |
| `trades` table never surfaced | `GET /api/trades` + a blotter | §8, §10 |
| SQLite blocking the event loop | WAL, short-lived connections, threadpool dispatch | §7 |
| Unbounded snapshot history query | `limit` / `since` parameters | §8 |
| Snapshot written on every trade | Dropped — a trade does not change total value | §7 |
| Unauthenticated LLM endpoint if deployed | Explicit warning on the deployment path | §11 |
| `backend/db/` collides with root `db/` | Renamed to `backend/app/db/` | §4 |
| Playwright container was heavy infrastructure | Host-run Playwright is the documented path | §12 |
| Redundant `docker-compose.yml` | Removed; the scripts are the only launch path | §4, §11 |
| Realized P&L absent — oversight or intent? | Intentional, and now stated so no agent adds it | §7 |
| Ticker casing, cash rounding, absent FKs | Stated explicitly | §6, §7 |
| Two different `.env` mechanisms conflated | Both documented and distinguished | §5 |
| LLM schema fields "optional" | All three required, empty arrays for none | §9 |
| No request/response shapes anywhere | §8 rewritten as a full contract | §8 |

### Corrected during the review

Two findings were wrong once checked against the shipped code, and the plan now documents what actually exists:

- **SSE already sends a full snapshot on connect.** `_generate_events` starts at `last_version = -1`, so the first iteration always pushes the entire cache — including after a reconnect. No snapshot endpoint or event type is needed. The payload is also a **map keyed by ticker in one event**, not one event per ticker, and it was worth freezing that in §6 before the frontend agent assumed otherwise.
- **Unknown tickers already work in the simulator.** `DEFAULT_PARAMS`, `CROSS_GROUP_CORR`, and a `random.uniform(50, 300)` seed price handle any symbol, and `add_ticker` writes to the cache immediately. Only API-level validation and the Massive-side behavior were missing.

### Still open

1. **Verify structured outputs on `gpt-oss-120b` via OpenRouter/Cerebras** before building the chat path (§9). The fallback is specified; the question is whether it is needed.
2. **Next.js is retained.** Static export disables essentially everything Next adds over a plain SPA, and Vite + React would be simpler and build faster in Docker — but this is a course capstone and the framework choice may be curricular. The concrete problems it caused are now fixed in place (§10 export config, §11 mount ordering). Worth a deliberate decision by the plan owner rather than a silent swap.
3. **Three TODOs** are new work introduced by this review, all backend: the SSE keepalive, the rolling price history plus its endpoint, and `POST /api/reset`.
