# History

Session-by-session log of what changed and why. Newest first. This is the
authoritative "what actually happened" record — TASKS.md checkboxes are stale.

---

## 2026-05-15 — Backtest engine completion (A.9)

The core event-driven engine (`ea/backtest/engine.py`) + CLI command + 3
tests already existed from a prior autonomous pass — REMAINING.md/ROADMAP were
stale on this. Closed the genuine gaps:

- `ea/backtest/__init__.py` was empty → now exports `BacktestEngine`,
  `BacktestResult`, `Trade`, `run_walk_forward`, `WalkForwardResult`,
  `write_report`.
- `ea/backtest/walkforward.py` — segmented out-of-sample windows run as
  independent backtests with compounded equity (surfaces regime instability
  instead of averaging it away). Rule-based strategies aren't param-fit, so
  "walk-forward" here = consecutive OOS windows, not train/test optimization.
- `ea/backtest/report.py` — `write_report()` emits `reports/<label>_<ts>.md`
  (+ `.json`) for later review and paper-vs-backtest gate comparison.
- CLI `ea backtest`: added `--walk-forward`, `--window-days`, `--report`, and
  `smc` to the strategy map. SMC is constructed with `timeframe="1Day"` since
  the engine replays 1Day bars (its default 1Hour would never fire).
- Added `test_walk_forward_compounds_and_reports`. Full suite: 89 passed.

Cached-news replay (news-driven backtests) remains deliberately deferred —
needs cached LLM analyses paired to bar dates.

---

## 2026-05-15 — Bug-fix + 1Hour migration session

Triggered by user reports: "more symbols not visible / cancel not working /
no live data", then "scanner only shows 4 / 12 / none in zone".

### Bugs fixed

1. **Crypto symbols showed no data.** `state.py` `watchlist_snapshot()` and
   `chart_data()` hardcoded `AssetClass.STOCK`, so any `BTC/USD`-style symbol
   returned empty. Added `_asset_class_for(symbol)` helper (`"/"` → crypto).

2. **Cancel trade silently failed.** `client.py` passed a `str` to alpaca-py
   `cancel_order_by_id`, which expects `UUID` → validation error swallowed by
   the frontend. Now coerces `uuid.UUID(order_id)`. `dashboard.js` cancel
   handler had no error branch — added an `alert()` on non-OK response.

3. **No live data.** Server lifespan never auto-started a stream; users had to
   click "⊕ Stock/Crypto" manually. `server.py` now auto-starts stock + crypto
   streams for the watchlist on boot (errors surface as alerts, non-fatal).

4. **Scanner showed only 4 symbols.** Not an Alpaca limit — the bar store was
   nearly empty (no backfill had been run). Created
   `scripts/backfill_universe.py`. Fixed a NaN-close crash in
   `backfill.py` (yfinance forex returns NaN rows on weekends/holidays) by
   `dropna(subset=OHLC)` before upsert.

5. **`_best_zone` impossible geometry (root cause of "never in zone").**
   `smc/strategy.py` selected a bullish zone only if `zone.high <=
   current_price` (entire zone below price), then checked `in_zone =
   low <= price <= high` — satisfiable only if `price == zone.high` exactly.
   Rewrote: keep zones whose `low <= price` (bullish) / `high >= price`
   (bearish) so price can actually be inside the selected zone.

6. **Microscopic zones + tight bands.** Raw FVGs were ~0.1% wide → "in zone"
   unreachable. Added `MIN_ZONE_PCT = 0.4%` padding around zone midpoint.
   Widened "approaching" band 2% → 4%.

### Migrations / changes

- **Scanner timeframe Day → 1Hour** per user request. `SMCScanner` now takes
  a `timeframe` param (default `"1Hour"`); `server.py` passes `"1Hour"`;
  `SMCStrategy` filters on `self.timeframe` instead of hardcoded `"1Day"`;
  `backfill.py` yfinance fallback now supports intraday intervals.
- `scripts/backfill_universe.py` backfills both 1D (charts) and 1H (scanner).
- Default scanner universe expanded 22 → 61 symbols (50 stock / 5 crypto /
  6 forex). MATIC/USD removed (delisted → rebranded POL).
- Dashboard default scanner filter changed "In zone" → "All status" (was
  showing a blank list because nothing is ever in-zone at rest).

### Outcome / decisions

- Backfilled 61 symbols at 1D + 1H (~43.5k rows).
- Setup density: ~12 on 1D, ~2 on 1H, 0 in-zone — **confirmed correct SMC
  behavior**, not a bug. SMC waits for retracement; most symbols are mid-trend.
- **User decision: keep confluence strict** (declined loosening / 15Min). Do
  not relax SMC gates without re-confirming.
- STATUS.md gaps now resolved: live bars *are* persisted to DuckDB
  (`state.consume_bus` upserts); daily equity anchor persists via
  `equity_baseline.py`.

---

## (pre-2026-05-15) — Autonomous Phase A pass

See `STATUS.md` for the full snapshot. Summary: broker abstraction, data
store + backfill, live stream, news pipeline (SEC/RSS/Yahoo + dedupe +
ticker tag), two-tier LLM analyzer, strategy framework, S2 news-momentum +
cross-sectional momentum + SMC strategies, risk manager (sizing + breakers),
idempotent order manager, signal consumer, dashboard, CLI. Test suite green
at end of that pass. Backtest engine (A.9) and PEAD/extra strategies (A.10)
deferred.
