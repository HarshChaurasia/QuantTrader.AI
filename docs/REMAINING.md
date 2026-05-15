# Remaining work

Ordered. "Start paper trading" needs almost nothing — it's operational, not
code. The substantive backlog is about *validating* before scaling.

---

## A. To START paper trading (no code blockers)

The stack runs today. These are operational steps, not tasks to build.

- [ ] **Backfill fresh data** — `python scripts/backfill_universe.py`
      (stop the dashboard first; DuckDB single-writer).
- [ ] **Start the stack** — `.venv\Scripts\ea paper` (autosubmit OFF).
- [ ] **Run during US market hours** — stock stream needs market open for
      bars; crypto streams 24/7. Outside hours the scanner only has stale bars.
- [ ] **Observe 1+ week with autosubmit OFF** — watch Signals + Scanner +
      News tabs. Confirm signals fire and look sane before letting it trade.
      (This is the STATUS.md "next session" recommendation.)
- [ ] **Then enable autosubmit** — dashboard toggle or `ea paper --autosubmit`.

That's it to be "paper trading". Everything below is to trust the results.

---

## B. Validation gaps (do before scaling / before live)

- [x] ~~**Backtest engine (T A.9).**~~ Built. `ea/backtest/`: event-driven
      daily-bar replay (`engine.py`) reusing Strategy/Risk/Order + cost model
      (commission/slippage) + metrics; walk-forward harness (`walkforward.py`,
      segmented out-of-sample, compounded); report-to-disk (`report.py`,
      md+JSON). Wired into `ea backtest` (`--walk-forward`, `--report`,
      strategies incl. `smc`). Cached-news replay still deferred (needs LLM
      analyses paired to bar dates).
- [ ] **30-trading-day paper run (T A.12).** Calendar gate, not code. Daily
      EOD review; compare P&L distribution to backtest expectation. Gate
      decision: proceed to Phase B / iterate / kill.
- [ ] **EOD report + alerts (T A.11 remainder).** `ea/monitoring/reports.py`
      (daily markdown to disk) and `ea/monitoring/alerts.py` (breaker /
      disconnect / unfilled-order). Needed to actually review a 30-day run.

## C. Strategy backlog (framework ready, implementations missing)

- [ ] **S1 PEAD** — `ea/strategies/pead.py`. Needs an earnings/transcript
      data source.
- [ ] Cross-sectional momentum exists; PEAD is the main missing stock strategy.
- [ ] **Phase B crypto strategies** — broker + stream already work for crypto;
      need C1 catalyst-momentum, C2 cross-sectional, C3 BTC-dominance overlay,
      plus crypto news fetchers (CoinDesk/TheBlock/Decrypt/Reddit).
- [ ] **Phase C forex** — OANDA adapter (`ea/brokers/oanda/`), macro/CB news
      fetchers, FX strategies. Currently forex bars come from yfinance only;
      no forex execution path.

## D. Known smaller gaps (from STATUS.md, re-verified)

- [ ] **Risk `_atr_pct` fallback** — falls back to flat 2% when bars missing;
      too crude for unstreamed-symbol intraday news. Add a live-price path.
- [ ] **Yahoo news schema** — yfinance news format drifts across versions;
      defensive parser needs a real-data sanity check during paper run.
- [x] ~~Live bars not persisted to DuckDB~~ — fixed; `state.consume_bus`
      upserts stream bars.
- [x] ~~Daily equity anchor resets on restart~~ — fixed via
      `equity_baseline.py`.

## E. Housekeeping

- [ ] **TASKS.md checkboxes are stale** (all `[ ]` despite Phase A done).
      Either update them or delete TASKS.md in favor of this file + HISTORY.md.
- [ ] MCP server for chat-with-system — explicitly post-Phase-A; low priority.

---

## Recommended next session

1. Do section **A** (start observing paper — costs nothing, starts the clock).
2. Backtest engine is now built — run S2/SMC/xsection backtests over 3+ yrs of
   backfilled bars (`ea backtest --walk-forward --report`) and compare the
   distribution to the live paper run.
3. Build **EOD report + alerts** (section B remainder) to make a 30-day run
   reviewable.
4. Defer C/D until a 30-day paper run has produced data worth comparing.
