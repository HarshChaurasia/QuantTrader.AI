# Roadmap

Phased delivery with explicit validation gates. **No phase advances without its gate passing.** This is the discipline that separates "system that makes money" from "system that looks good in backtest."

## Phase 0 — Foundation (week 1)

**Goal:** Working dev environment, clean architecture skeleton, broker abstraction in place, no trading logic yet.

Deliverables:
- Python project (uv or poetry), `pyproject.toml`, lockfile.
- Config system (pydantic settings, YAML per env).
- Secrets via `.env` (never committed); `.env.example` checked in.
- Logging (loguru), structured with run-id correlation.
- `Broker` ABC + `AlpacaBroker` skeleton (auth, get_account, get_positions — read-only).
- Test harness (pytest), CI-ready even if no CI yet.
- `cli.py` entry point with sub-commands stubbed.

**Gate:** `uv run pytest` passes; `uv run ea status` connects to Alpaca paper and prints account state.

## Phase A — US Stocks end-to-end (weeks 2-5)

The hard one. Once this works, B and C are mostly adapter swaps.

**A.1 — Market data**
- Universe scan (liquidity filter, ~1500-2000 names).
- Historical bar backfill (DuckDB).
- Real-time bar/quote stream from Alpaca.

**A.2 — News pipeline**
- SEC EDGAR fetcher.
- Yahoo News + Reuters/MarketWatch RSS.
- Dedup + ticker-tag.
- Claude analyzer (Haiku scoring + Sonnet promotion).
- News cache.

**A.3 — Strategy framework**
- Event bus, `Strategy` base class.
- Implement S1 (PEAD) and S2 (catalyst news momentum).
- (S3 cross-sectional momentum can come in A.5.)

**A.4 — Risk + execution**
- Risk manager with all 6 layers.
- Order manager (idempotent, reconciling).
- Position tracker.

**A.5 — Backtest** — ✅ *delivered (see docs/HISTORY.md)*
- Event-driven engine with cost model. ✅
- Walk-forward harness. ✅
- Performance metrics + per-strategy attribution. ✅
- Any-timeframe replay + report-to-disk. ✅
- *Remaining:* run multi-year S2/SMC/scalp backtests and compare to paper
  (data exercise, not code); news-driven replay still deferred.

**A.6 — Paper trading** — ✅ *delivered*
- Wire paper Alpaca to live system. ✅
- Monitoring dashboard (incl. Scalping view). ✅
- Daily EOD reports + operational alert monitor. ✅

**Gate:** 30 trading days of paper trading with:
- Sharpe > 0.5 net of estimated costs
- Max drawdown < 8%
- No unhandled errors in production logs
- Paper P&L within reasonable distance of backtest expectation (no >2σ deviation)

If gate fails: diagnose (strategy, costs, regime, bug) before Phase B.

## Phase B — Crypto (weeks 6-7)

**B.1** — Add Alpaca crypto broker support (mostly config; same SDK).
**B.2** — Crypto news fetchers (CoinDesk, The Block, Decrypt, Reddit).
**B.3** — Implement C1 (catalyst momentum), C2 (cross-sectional crypto momentum).
**B.4** — 24/7 mode in event bus + risk manager.
**B.5** — Backtest crypto strategies.
**B.6** — Add crypto to paper portfolio.

**Gate:** 30 days paper trading with combined stocks+crypto portfolio. Combined Sharpe > stocks-only Sharpe (proves diversification helped).

## Phase C — Forex (weeks 8-9)

**C.1** — `OandaBroker` adapter, OANDA demo account, FX-specific data conventions.
**C.2** — Central bank fetchers + economic calendar.
**C.3** — Implement F1 (carry), F2 (CB statement momentum), F3 (trend on majors).
**C.4** — FX-specific risk (leverage caps, pip values, swap costs).
**C.5** — Backtest FX strategies.
**C.6** — Add forex to paper portfolio.

**Gate:** 30 days paper of full multi-asset portfolio. Full-portfolio Sharpe > Phase B Sharpe.

## Phase D — Live (after all paper gates pass)

**D.1** — Final review: walk through every code path, every circuit breaker, every reconciliation point.
**D.2** — Tax/accounting decisions; record-keeping for live trades.
**D.3** — Decide live capital amount. Recommend starting with 10-20% of intended size.
**D.4** — Switch config to live keys. Same code path.
**D.5** — Watch like a hawk for 2 weeks: every fill, every signal, every divergence from paper.
**D.6** — Scale up gradually only after consistent live = paper.

**Gate:** Manual go/no-go after each scale-up step.

## Out of scope (until later)

- Multi-account / multi-user.
- Cloud deployment, k8s.
- Options.
- Crypto perp / futures.
- Mobile app.
- Marketing the system to anyone.

## Anti-goals

We will not:
- Add a strategy "because it sounds cool" without a stated edge hypothesis.
- Skip a validation gate to "get to live faster."
- Optimize parameters until backtest looks great — that's how overfitting happens.
- Add complexity without a paper-validated reason.
