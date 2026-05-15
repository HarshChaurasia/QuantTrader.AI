# Status — end of autonomous Phase A pass

## What works end-to-end

Run `uv run ea paper` (or `ea dashboard`) and the following stack starts:

1. **Broker abstraction** (`AlpacaBroker`) — account, positions, bars, submit/cancel orders, close-all
2. **Market data**
   - Historical: DuckDB store, backfill from Alpaca + yfinance fallback (`ea data backfill`)
   - Live: WebSocket stream for stocks (IEX feed) and crypto (24/7) → `BarEvent` on the bus
3. **Event bus** — async pub/sub, drop-oldest backpressure, thread-safe publish from the alpaca-py thread
4. **News pipeline**
   - Fetchers: SEC EDGAR 8-K RSS, generic RSS (Reuters/MarketWatch/CoinDesk/SEC press), yfinance per-ticker
   - Dedupe: SQLite content-hash with 72h TTL
   - Ticker tagging: parenthesized + universe-validated heuristic
   - Poller: schedules all fetchers concurrently
5. **LLM analyzer** (graceful no-op without `ANTHROPIC_API_KEY`)
   - Tier 1: Haiku scores relevance/sentiment/materiality
   - Tier 2: Sonnet promoted on items above threshold for catalyst classification + direction
   - SQLite cache (never re-score), daily spend telemetry with cap
6. **Strategies**
   - Framework: `Strategy` ABC, `Signal` dataclass, `StrategyRunner` (subscribe → dispatch → emit)
   - **S2 News momentum** — fires on LLM-scored material catalysts above thresholds
7. **Risk manager**
   - Vol-targeted risk parity sizing (ATR-based)
   - Daily/weekly loss circuit breakers
   - Position-count, per-asset-class, per-position caps
   - Rejects doubling existing same-direction positions
   - Strategy budget degradation after 5 consecutive losses
8. **Order manager**
   - Idempotent submission via `client_order_id`
   - Retry on transient errors, no retry on permanent (duplicate IDs, buying power)
   - Recent-orders log for dashboard
9. **Signal consumer**
   - Bus.Signal → risk.evaluate → if accepted + autosubmit, order_manager.submit
   - Manual autosubmit toggle in dashboard
10. **Dashboard** (`http://127.0.0.1:8765`)
    - Account, Positions, Watchlist (live ticks + charts), News (LLM-scored), Signals, Risk breakers, Event log, System monitor
    - Controls: stream start/stop (stock/crypto), watchlist add/remove, manual order entry, strategy pause/resume, autosubmit toggle, kill switch (now actually cancels orders + closes positions)
    - Live updates via SSE
11. **CLI**
    - `ea version`, `ea status`, `ea dashboard`, `ea paper`
    - `ea data {backfill,universe,assets,info,stream}`
    - `ea news poll`

## Test coverage

72 tests passing, including:
- Broker models + Alpaca adapter (with live paper smoke)
- Config (paper/live merge, live-disabled safety)
- Logging (file rotation, run-id correlation)
- Data store (idempotent upsert, range queries)
- Universe filtering
- Event bus (publish, backpressure, thread-safe)
- Alpaca stream (handler → bus normalization)
- Dashboard server (all REST endpoints, template render, kill switch)
- News pipeline (dedupe, ticker tagging, poller with mocked fetchers)
- Strategies (news momentum threshold logic)
- Risk manager (sizing, circuit breakers, doubling rejection)
- Signal consumer (full flow + autosubmit toggle)

## Deferred (backlog)

- **T A.9 Backtest engine** — event-driven replay, cost model, walk-forward harness. Substantial standalone tool. Currently we can only validate via live paper.
- **T A.10 S1 (PEAD) + S3 (Cross-sectional momentum)** — additional strategies. Framework ready; just need implementations + earnings data source for PEAD.
- **Phase B (Crypto strategies)** — C1 catalyst momentum, C2 cross-sectional crypto (broker + stream already work for crypto; just need strategies).
- **Phase C (Forex)** — OANDA adapter + carry/CB-momentum strategies.
- **MCP server** — post-Phase-A; thin wrapper around dashboard state for chat-with-system.

## What to do next session

Three productive directions:
1. **Get paper-trading observations.** Run `ea paper --autosubmit=false` for a week. Watch what signals fire (need real news to flow through SEC/Yahoo/RSS). Decide if news_momentum is producing usable signals before tuning thresholds.
2. **Build backtest engine** (T A.9) so we can validate strategies on historical news + bars before committing more compute to live paper.
3. **Add cross-sectional momentum** (T A.10 part) — pure-technical strategy that doesn't need LLM; smooths the equity curve when news days are quiet.

## Known gaps to revisit

- News fetchers' Yahoo schema parser is defensive but yfinance's news format changed across versions — verify the live output makes sense once paper trades a few days
- Stream → backfill: live bars from the stream aren't being persisted to DuckDB (only cached in state.latest_bars). Add a writer task for paper-trading continuity.
- Position reconciliation loop: dashboard's `state.starting_equity_today` resets on process restart — persist daily anchor to disk.
- Risk manager `_atr_pct` falls back to 2% when bars missing — that's overly defensive for unstreamed-symbol intraday news; needs live-price fallback path.
