# Operations runbook

How to run the system day-to-day. Windows + PowerShell. All commands from the
repo root: `D:\H\Projects\QuantTrader.AI`.

> **DuckDB is single-writer.** Stop the dashboard before running any backfill
> or `ea report` against the same store, or it errors with "file is being
> used by another process".

> If a script prints SMC confluence arrows (↑↓) and dies on cp1252:
> `$env:PYTHONIOENCODING="utf-8"`.

---

## 1. Backfill data (do first / refresh daily)

```powershell
.venv\Scripts\python scripts/backfill_universe.py             # 1D + 1H, 61 symbols
.venv\Scripts\python scripts/backfill_universe.py --no-daily   # 1H only
```

The scalper also cold-starts its own 1Min crypto backfill on server boot, and
polls yfinance for 1Min forex every 60s — no manual step needed for those.

Store: `data/bars.duckdb` (gitignored).

## 2. Start / stop paper trading

```powershell
.venv\Scripts\ea paper                 # full stack, autosubmit OFF (observe first)
.venv\Scripts\ea paper --autosubmit    # only AFTER the observation period
```

- Dashboard: <http://127.0.0.1:8765>
- Ctrl+C to stop. On boot it auto-starts stock + crypto streams, the news
  poller, the SMC scanner, all four strategies, and the alert monitor.
- For a real multi-day run, launch it in your own terminal so it outlives any
  tooling session.

**Strategies running in parallel:** `news_momentum`, `xsection_momentum`,
`smc` (1Hour swing), `smc_scalp` (1Min, crypto+forex).

## 3. Dashboard views

| View | What |
|---|---|
| Chart | Price chart + bottom panel: **Positions / Orders / Signals / Activity** — the unified cross-strategy ledger (each row tagged by strategy). |
| Scanner | SMC scanner table (1Hour), status filter, scan-now. |
| News | LLM-scored news feed. |
| **Scalping** | `smc_scalp` focus: open scalp positions, recent scalp signals, scalper on/off toggle. History still lives in the Chart panels. |
| Monitor | Risk breakers, system stats, activity log. |

Controls: KILL switch (cancel all + flatten, 2-step confirm), Auto-submit
toggle, manual order ticket (risk-validated, no bypass), per-strategy
pause/resume chips.

## 4. Backtest

```powershell
# Daily swing SMC
.venv\Scripts\ea backtest --symbols SPY,QQQ,AAPL --strategies smc `
  --timeframe 1Day --start 2023-01-01 --walk-forward --report

# 1Min crypto scalper
.venv\Scripts\ea backtest --symbols BTC/USD,ETH/USD --strategies smc_scalp `
  --timeframe 1Min --asset-class crypto --walk-forward --report
```

Flags: `--strategies` (`xsection_momentum,news_momentum,smc,smc_scalp`),
`--timeframe` (1Min/5Min/15Min/1Hour/1Day), `--asset-class`
(stock/crypto/forex), `--walk-forward`, `--window-days`, `--report`.
Reports land in `./reports/` (markdown + JSON). News-driven replay is deferred.

## 5. End-of-day report

```powershell
.venv\Scripts\ea report                      # standalone (stop the server first)
```
or while the server runs: `POST http://127.0.0.1:8765/api/report/eod`.
Writes `reports/eod_<YYYY-MM-DD>.md` (account, positions, today's orders,
signals-by-strategy, risk, alerts).

## 6. Alerts

`AlertMonitor` runs inside the server (60s loop, deduped) and raises dashboard
alerts on: circuit-breaker trip, stream disconnect, stale/errored orders.
No setup; watch the Activity log / Monitor view. Webhooks not yet wired.

## 7. Going live (gated — do not skip)

Live is blocked behind Phase D and all paper gates (see `docs/ROADMAP.md` /
`docs/REMAINING.md`). `ea live` is intentionally a stub until then.

## Constraints to remember

- **PDT:** stock day-trading on a <$25k account trips Pattern Day Trader
  rules; the scalper is therefore crypto+forex only.
- **Forex is signal-only** until an OANDA adapter exists (Phase C) — forex
  scalp signals generate but cannot auto-execute.
- **Survival > optimization.** Don't loosen SMC confluence or skip a
  validation gate to "get to live faster".
