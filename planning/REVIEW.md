# Review: changes since last commit

## Findings

### High - First-launch setup is still not coherent

`planning/PLAN.md:15-20` promises a single Docker command/start script and an AI chat panel that is ready immediately. The environment contract then marks `OPENROUTER_API_KEY` as required (`planning/PLAN.md:128-137`), the README tells users to add that key before running (`README.md:29-35`), and `planning/PLAN.md:487` states that the key exists in the project-root `.env`. At the same time, the plan says `LLM_MOCK=true` works without an API key (`planning/PLAN.md:557-568`) and E2E runs in mock mode (`planning/PLAN.md:691-693`).

That leaves first launch ambiguous: either the app can boot and show a usable mocked/degraded chat without OpenRouter credentials, or the "ready to assist" experience requires a secret the user must supply. Make `OPENROUTER_API_KEY` required only when `LLM_MOCK != true`, commit/document a real `.env.example`, and define the default first-launch mode.

### High - Persisted portfolios need startup market-source rehydration

The plan correctly defines the tracked ticker set as `watchlist union non-zero positions` (`planning/PLAN.md:183-194`) and says the SQLite database persists across container restarts (`planning/PLAN.md:638-644`). The explicit add/remove rules only cover watchlist/trade requests, and reset performs a re-sync (`planning/PLAN.md:345`), but there is no startup/lifespan rule that loads persisted watchlist and position tickers from SQLite into the market data source.

After a restart with existing holdings, the database can contain positions while the market source only tracks defaults. That would leave off-watchlist holdings without live prices, cause `GET /api/portfolio` to fall back to `avg_cost`, and make snapshots stall under the "skip if any held ticker has no cached price" rule. Add a startup reconciliation step: after DB initialization and before serving streams, load `watchlist union positions(quantity > 0)` and call `source.add_ticker()` for each.

### High - Backend writes need serialization, not just frontend throttling

The plan defines cash/position/trade mutations (`planning/PLAN.md:404-427`), LLM auto-execution (`planning/PLAN.md:522-529`), reset (`planning/PLAN.md:345`), and short-lived SQLite connections dispatched off the event loop (`planning/PLAN.md:247-253`). It only bounds chat concurrency in the frontend (`planning/PLAN.md:504`), which does not protect manual trades, multiple browser tabs, direct API calls, or reset racing with trade/chat actions.

Require a backend transaction boundary around each trade, such as `BEGIN IMMEDIATE`, so the cash check, cash update, position update/delete, and trade insert succeed or fail together. Also route manual trades, LLM trades, watchlist changes that affect the market source, reset, and snapshot writes through one per-user write lock or equivalent service path so they cannot observe or create half-applied state.

### Medium - The new review agent delegates to a nested Codex process

`.claude/agents/change-reviewer.md:6-11` tells the subagent not to review changes itself and instead to run `codex exec "Please review all changes since the last commit and write your feedback to planning/REVIEW.md"`. That makes the Claude agent a wrapper around another autonomous writer of the same file, with no guard against overwriting existing feedback, no status propagation, and no fallback if `codex` is unavailable in the caller's shell.

If this agent is meant to provide independent review, have it perform the review directly from the current worktree and write findings. If the intent is specifically "invoke Codex", make that explicit in the command name/description and add failure handling so users do not get a silent no-op or a clobbered review file.

### Medium - Malformed LLM fallback violates the chat response contract

`POST /api/chat` normally returns `id`, `message`, `actions`, and `created_at` (`planning/PLAN.md:457-472`). The malformed-response fallback returns only `message` and `actions` (`planning/PLAN.md:538-544`), and the plan does not say whether the user/assistant messages are stored when fallback is used.

Make the fallback return the exact same envelope as the success path, including `id` and `created_at`, with empty action arrays. Also specify persistence behavior: either store both messages so refresh matches what the user saw, or explicitly do not store fallback responses and adjust frontend/test expectations.

### Medium - AI watchlist removal is promised but not specified

The UX promises watchlist add/remove manually or via AI chat (`planning/PLAN.md:31-32`). Manual add/remove endpoints are specified (`planning/PLAN.md:451-453`), but the LLM schema only shows `{"action": "add"}` and never defines allowed watchlist action values (`planning/PLAN.md:506-520`). The mock mode also only adds PYPL (`planning/PLAN.md:561-566`).

Define `watchlist_changes[].action` as `"add" | "remove"` and specify result behavior for duplicate adds, removing a missing ticker, and removing a ticker that is still held. If AI removal is not intended for v1, remove that promise from the UX section.

### Medium - P&L history can be empty on first launch and after reset

The P&L chart reads from `GET /api/portfolio/history` (`planning/PLAN.md:581`), snapshots are written every 30 seconds (`planning/PLAN.md:310-315`), and reset truncates `portfolio_snapshots` (`planning/PLAN.md:345`). The E2E list expects the P&L chart to have data points (`planning/PLAN.md:701`), but after a fresh DB or reset there may be no point until the first snapshot tick.

Either seed/write an initial baseline snapshot during DB initialization and after reset, or specify a frontend empty state and update E2E expectations to wait for the first snapshot. For a demo app, a baseline point is simpler and makes the chart feel intentionally populated.

### Medium - Quantity precision is stated but not enforceable as written

The schema stores quantities as SQLite `REAL` (`planning/PLAN.md:278-294`), REST says quantities have up to 6 decimals (`planning/PLAN.md:355-356`), and validation only rejects non-positive/NaN/infinite values (`planning/PLAN.md:416-425`). There is no rule for `0.0000004`, `1.1234567`, or how rounding should affect cash and average cost.

Add an API-boundary rule for quantity scale: reject more than 6 decimal places with a specific `400` message, or round to 6 decimals and document it. If exact behavior matters, use `Decimal` in service code or store integer micro-shares/cents while still returning JSON numbers.

### Low - The README bind-mount command is POSIX-only without saying so

The README changed the quick-start command to `docker run -v "$(pwd)/db:/app/db" ...` (`README.md:33-35`). That matches the plan's macOS/Linux path, but the README presents it as the only quick start. The plan separately says Windows scripts need `${PWD}` quoting because spaces are common (`planning/PLAN.md:644`), and those scripts are also listed as the supported launch path (`planning/PLAN.md:646-660`).

Either label the README command as macOS/Linux and add the Windows command/script path, or make README quick start defer to the start scripts once they exist. The previous named-volume command was platform-neutral; the bind mount needs platform-specific documentation.

### Low - The uniform error envelope needs framework-validation coverage

The API convention says every non-2xx response is `{"error": "human readable message"}` and FastAPI's default `422` body is replaced (`planning/PLAN.md:353-356`). The plan does not define status/message behavior for malformed JSON, missing required fields, wrong types, invalid query parameters, or path parameter validation.

Add a short table for framework-level validation errors and require a `RequestValidationError`/JSON decode handler that maps those cases into the same envelope. This prevents frontend agents from special-casing FastAPI's default validation response.

### Low - The new doc-review command is too loose to be reliable

`.claude/commands/doc-review.md:1` contains typos and says to review a planning file named `$ARGUMENTS`, then append "questions, clarifications or feedback" to a new section. It does not say what to do when the file is missing, whether `$ARGUMENTS` must be a basename under `planning/`, or what heading format should be used.

Tighten the command so it validates the target file under `planning/`, fails clearly when the argument is missing, and appends under a stable heading. That will make repeated doc reviews less likely to scatter duplicate sections.

## Notes

I did not flag `db/.gitkeep`; it matches the new bind-mount contract. The `.gitignore` additions for `db/*.db`, `db/*.db-wal`, and `db/*.db-shm` also match the planned SQLite/WAL runtime files.
