# QuantTrader.AI — Claude working context

Read this first. It's the entry point for any Claude session on this repo.

## What this is

Python swing-trading system across **US stocks, crypto, forex** with an
LLM-augmented news pipeline and an SMC (Smart Money Concepts) scanner. Paper
trading via Alpaca. Survival-first: paper must pass gates before any live capital.

## Run it

```powershell
cd D:\H\Projects\QuantTrader.AI
.venv\Scripts\ea paper                 # dashboard + full stack, autosubmit OFF
.venv\Scripts\ea paper --autosubmit    # only after observation period
```
Dashboard: http://127.0.0.1:8765 · CLI: `.venv\Scripts\ea --help`

**DuckDB is single-writer.** Stop the dashboard before running any backfill
script, or it errors with "file is being used by another process".

## Data backfill

```powershell
.venv\Scripts\python scripts/backfill_universe.py            # 1D (90d) + 1H (60d), 61 symbols
.venv\Scripts\python scripts/backfill_universe.py --no-daily  # 1H only
```
Store: `data/bars.duckdb` (gitignored). Re-run daily to keep fresh.

## Key paths

| Area | Path |
|---|---|
| FastAPI server + routes | `ea/monitoring/server.py` |
| Dashboard state aggregator | `ea/monitoring/state.py` |
| Frontend | `ea/monitoring/static/js/dashboard.js`, `templates/index.html` |
| Alpaca adapter | `ea/brokers/alpaca/client.py`, `stream.py` |
| SMC scanner | `ea/scanner/smc_scanner.py` |
| SMC pattern math | `ea/strategies/smc/strategy.py`, `smc/patterns.py` |
| Risk / orders | `ea/risk/manager.py`, `ea/execution/order_manager.py` |
| Signal wiring | `ea/execution/signal_consumer.py` |
| Config | `ea/config.py`, `config/paper.yaml`, `config/live.yaml` |
| Backfill | `ea/data/backfill.py`, `scripts/backfill_universe.py` |

## Current state (verified 2026-05-15)

- Stack runs end-to-end via `ea paper`. Functionally Phase A complete.
- `.env` has Alpaca paper keys + `GEMINI_API_KEY` set (LLM provider = gemini).
  Anthropic/OANDA empty by design (Anthropic optional, OANDA is Phase C).
- 61 symbols backfilled at 1D + 1H. Scanner runs on **1Hour** bars.
- **No hard code blocker to start paper trading.** What remains is operational
  (run during market hours, observe, then enable autosubmit) — see
  `docs/REMAINING.md`.

## Conventions

- Windows + PowerShell. Use PowerShell syntax. `$env:PYTHONIOENCODING="utf-8"`
  when a script prints SMC confluence arrows (↑↓) or it dies on cp1252.
- Asset class from symbol: `"/" in symbol` → crypto, else stock. Helper:
  `state._asset_class_for()`. Never hardcode `AssetClass.STOCK`.
- Don't loosen SMC confluence without explicit user approval — user chose
  "keep strict" (SMC is wait-for-retracement; sparse setups are correct).
- TASKS.md checkboxes are stale (all `[ ]`) — trust STATUS.md / this file /
  `docs/HISTORY.md` for actual state, not TASKS.md checkboxes.

## Pointers

- `docs/HISTORY.md` — what's been done, session-by-session, with rationale.
- `docs/REMAINING.md` — ordered list of what's left to start + scale paper.
- `STATUS.md` — end-of-Phase-A snapshot (some gaps now fixed; see HISTORY).
- `docs/ARCHITECTURE.md`, `RISK.md`, `STRATEGIES.md`, `ROADMAP.md` — design.
