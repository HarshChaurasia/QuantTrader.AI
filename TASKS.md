# Tasks

Granular, ordered, with confirmation gates. Status legend: `[ ]` not started, `[~]` in progress, `[x]` done, `[!]` blocked.

**Confirmation rule:** Claude pauses and asks before starting each numbered task. Sub-bullets within a confirmed task can proceed without re-asking.

---

## Phase 0 — Foundation

### T0.1 — Project scaffold and Python environment
- [ ] Initialize Python project with `uv` (preferred) or `poetry`.
- [ ] `pyproject.toml` with dependencies: `alpaca-py`, `anthropic`, `pydantic`, `pydantic-settings`, `pandas`, `polars`, `duckdb`, `loguru`, `httpx`, `pytest`, `pytest-asyncio`, `python-dotenv`, `pyyaml`.
- [ ] `.gitignore` (data/, .env, __pycache__, .venv, etc.).
- [ ] `.env.example` listing required env vars (ALPACA_KEY_ID, ALPACA_SECRET_KEY, ANTHROPIC_API_KEY).
- [ ] Directory layout per ARCHITECTURE.md.
- [ ] Smoke test: `pytest` runs, finds zero tests, exits clean.

### T0.2 — Config + secrets + logging
- [ ] `ea/config.py` — pydantic Settings model loading from env + YAML.
- [ ] `config/paper.yaml`, `config/live.yaml` with safe defaults.
- [ ] `ea/logging.py` — loguru sinks (console + rotating file), run-id correlation.
- [ ] Test: load config, log an event, assert log file contains it.

### T0.3 — Broker abstraction + Alpaca read-only adapter
- [ ] `ea/brokers/base.py` — `Broker` ABC with method signatures from ARCHITECTURE.md.
- [ ] `ea/brokers/models.py` — pydantic models: `Account`, `Position`, `Order`, `OrderRequest`, `BrokerEvent`.
- [ ] `ea/brokers/alpaca/client.py` — `AlpacaBroker` implementing read-only methods (`get_account`, `get_positions`, `get_bars`).
- [ ] `ea/cli.py` — `ea status` sub-command that prints account + positions.
- [ ] Test: mock-based unit tests for adapter; manual smoke test against paper account.

### T0.4 — Initial CLI and entry points
- [ ] `ea status` — print account + positions.
- [ ] `ea backtest` — stub that prints "not yet implemented".
- [ ] `ea paper` — stub.
- [ ] `ea live` — stub with confirmation prompt.

**Phase 0 gate:** `ea status` connects to Alpaca paper and shows account info. All tests pass.

---

## Phase A — US Stocks end-to-end

### T A.1 — Universe and historical data
- [ ] `ea/data/universe.py` — daily liquidity scan (price > $5, ADV > $10M, exclude OTC/pink).
- [ ] `ea/data/store.py` — DuckDB schema for bars; partitioned by symbol+date.
- [ ] `ea/data/backfill.py` — historical bar backfill (Alpaca + yfinance fallback).
- [ ] CLI: `ea data backfill --start 2020-01-01`.

### T A.2 — Sci-fi dashboard (scaffold + initial panels)
- [ ] `ea/monitoring/server.py` — FastAPI app with REST + SSE endpoints, broker-aware state aggregator.
- [ ] `ea/monitoring/templates/index.html` — single-page UI shell.
- [ ] `ea/monitoring/static/css/dashboard.css` — sci-fi theme (dark, neon cyan/magenta, monospace, glow, scanlines).
- [ ] `ea/monitoring/static/js/dashboard.js` — frontend: fetch state, SSE subscription, chart rendering, control actions.
- [ ] Panels working day one: header bar, account, positions, watchlist mini-charts (from store), system monitor.
- [ ] Panels with "Awaiting Phase A.X" stub: news ticker, signals, risk breakers.
- [ ] Controls day one: kill switch (stubbed/logged), refresh toggle, manual symbol watchlist.
- [ ] CLI: `ea dashboard --host 127.0.0.1 --port 8765`.

### T A.3 — Real-time market data stream
- [ ] `ea/brokers/alpaca/stream.py` — WebSocket bar/quote subscriber, normalized to `BarEvent`.
- [ ] Reconnection + backpressure handling.
- [ ] Wire stream → dashboard watchlist (live tick updates on charts).
- [ ] Test: subscribe to SPY paper stream for 60s, assert events received.

### T A.4 — News pipeline foundation
- [ ] `ea/news/models.py` — `NewsItem`, `NewsAnalysis`, `NewsEvent`.
- [ ] `ea/news/fetchers/sec_edgar.py` — pull recent 8-Ks/10-Qs.
- [ ] `ea/news/fetchers/yahoo.py` — yfinance news per symbol.
- [ ] `ea/news/fetchers/rss.py` — generic RSS reader for Reuters/MarketWatch.
- [ ] `ea/news/dedupe.py` — content-hash dedup.
- [ ] `ea/news/ticker_tagger.py` — map articles to symbols (SEC has CIK→ticker; RSS uses heuristics).

### T A.5 — LLM news analyzer
- [ ] `ea/news/analyzer.py` — Claude API client with prompt caching.
- [ ] Two-tier scoring: Haiku for `(relevance, sentiment, materiality)`; promote to Sonnet for catalyst-type structured output.
- [ ] `ea/news/cache.py` — SQLite cache `(article_hash → analysis)`; never re-score.
- [ ] Cost telemetry: log per-article token usage, alert if daily spend > threshold.
- [ ] Test: feed a known earnings PR, assert non-zero materiality + correct catalyst type.

### T A.6 — Strategy framework + S2 (catalyst news momentum)
- [ ] `ea/strategies/base.py` — `Strategy` ABC, `Signal`, `Context`.
- [ ] `ea/eventbus.py` — async pub/sub with backpressure.
- [ ] `ea/strategies/news_momentum.py` — implement S2 from STRATEGIES.md.
- [ ] Unit tests with synthetic events.

### T A.7 — Risk manager
- [ ] `ea/risk/sizing.py` — vol-targeted risk parity sizing.
- [ ] `ea/risk/limits.py` — all 6 layers from RISK.md.
- [ ] `ea/risk/circuit_breakers.py` — daily/weekly loss, consecutive losses, etc.
- [ ] `ea/risk/manager.py` — orchestrator: pre-trade veto + post-trade state update.
- [ ] Tests: every breaker triggers correctly on synthetic histories.

### T A.8 — Order manager + position tracker
- [ ] `ea/execution/order_manager.py` — idempotent submission, retry-on-transient.
- [ ] `ea/execution/position_tracker.py` — local state + broker reconciliation loop.
- [ ] `ea/execution/router.py` — limit-vs-market routing logic.
- [ ] Crash-safe state journal.

### T A.9 — Backtest engine [DEFERRED]
- [ ] `ea/backtest/engine.py` — event-driven replay using same Strategy/Risk/Order code.
- [ ] `ea/backtest/cost_model.py` — slippage + commission per asset class.
- [ ] `ea/backtest/news_replay.py` — replay cached LLM analyses (no live API calls during backtest).
- [ ] `ea/backtest/metrics.py` — Sharpe, Sortino, max DD, profit factor, hit rate, per-strategy attribution.
- [ ] `ea/backtest/walkforward.py` — rolling-window validation.
- [ ] CLI: `ea backtest --strategy news_momentum --start 2022-01-01 --end 2025-12-31`.

### T A.10 — Implement S1 (PEAD) and S3 (cross-sectional momentum) [DEFERRED]
- [ ] `ea/strategies/pead.py` — PEAD with LLM transcript analysis.
- [ ] `ea/strategies/xsection_momentum.py` — monthly rebalance into top decile.
- [ ] Backtest both, generate report.

### T A.11 — Dashboard wire-up + paper trading mode
- [ ] Wire signals, news, risk-breaker panels to live data (replacing "Awaiting Phase A.X" stubs).
- [ ] Manual order entry → risk manager → order manager.
- [ ] Kill switch actually cancels open orders + closes positions.
- [ ] `ea/monitoring/reports.py` — EOD daily report (markdown to disk + optional webhook).
- [ ] `ea/monitoring/alerts.py` — circuit breaker / disconnect / unfilled-order alerts.
- [ ] CLI: `ea paper --strategies pead,news_momentum,xsection_momentum` (dashboard already exposes them).

### T A.12 — Phase A paper validation (calendar gate, not a coding task)
- [ ] Run paper for 30 trading days.
- [ ] Daily review of EOD reports.
- [ ] Compare paper P&L distribution to backtest expectation.
- [ ] **Gate decision: proceed to Phase B, iterate, or kill.**

---

## Phase B — Crypto

### T B.1 — Alpaca crypto support
- [ ] Extend `AlpacaBroker` for crypto endpoints.
- [ ] 24/7 mode in event bus + risk manager (no market-hours gate).
- [ ] Universe: top 30-50 USD-quoted alts by ADV.

### T B.2 — Crypto news pipeline
- [ ] Fetchers: CoinDesk, The Block, Decrypt RSS.
- [ ] Reddit fetcher (`praw`) for `r/CryptoCurrency` top posts.
- [ ] Token-tagger for crypto articles (different mapping than stocks).

### T B.3 — Crypto strategies
- [ ] `ea/strategies/crypto_news_momentum.py` (C1).
- [ ] `ea/strategies/crypto_xsection_momentum.py` (C2).
- [ ] BTC dominance regime overlay (C3).

### T B.4 — Backtest + paper
- [ ] Backtest crypto strategies over 2+ years.
- [ ] Add crypto to paper portfolio.
- [ ] Run combined paper for 30 days.

**Phase B gate:** combined Sharpe > stocks-only Sharpe.

---

## Phase C — Forex

### T C.1 — OANDA broker adapter
- [ ] `ea/brokers/oanda/client.py` — implement `Broker` interface with OANDA REST/stream.
- [ ] FX-specific: pip math, swap costs, leverage caps.
- [ ] OANDA demo account smoke test.

### T C.2 — Macro news pipeline
- [ ] Central bank press release fetchers (Fed, ECB, BoJ, BoE).
- [ ] ForexLive RSS.
- [ ] Economic calendar (Investing.com or alt source).

### T C.3 — Forex strategies
- [ ] `ea/strategies/fx_carry.py` (F1).
- [ ] `ea/strategies/fx_cb_momentum.py` (F2) — LLM parses CB statements.
- [ ] `ea/strategies/fx_trend.py` (F3).

### T C.4 — Backtest + paper
- [ ] Backtest FX strategies.
- [ ] Add FX to paper portfolio.
- [ ] 30-day combined paper run.

**Phase C gate:** full multi-asset Sharpe > Phase B Sharpe.

---

## Phase D — Live (after all paper gates)

### T D.1 — Pre-live audit
- [ ] Code walk: every order path, every breaker, every reconciliation.
- [ ] Tax/accounting: decide record-keeping format (CSV journal at minimum).
- [ ] Live capital decision: amount + ramp plan.

### T D.2 — Switch to live
- [ ] Live API keys in separate `.env.live` (never mixed with paper).
- [ ] First-week monitoring schedule (check every fill in real time).

### T D.3 — Scale-up gates
- [ ] Week 1-2: 10% of intended size.
- [ ] Week 3-4: 25% if live ≈ paper.
- [ ] Week 5+: 50% then 100% on continued match.

---

## Cross-cutting backlog

Things that emerge during work, not scheduled to a specific phase:

- **MCP server for chat-with-system** (post-Phase-A). Build a thin EA-specific MCP server that wraps our dashboard state aggregator: tools for `get_account`, `get_positions`, `get_recent_signals`, `get_recent_news_scored`, `get_risk_state`, `pause_strategy`, `arm_kill_switch` (requires explicit confirmation arg). Reuses our state layer — does NOT replace `alpaca-py` in the trading core (MCP would add latency/failure surface there). Only useful once we have project-specific concepts (signals, scored news, breakers) to expose; before A.5/A.6/A.7 it would just be a clone of the dashboard's broker endpoints.
