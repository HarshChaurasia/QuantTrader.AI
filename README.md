# QuantTrader.AI

A multi-asset swing-trading system for **US stocks, crypto, and forex**, combining
technical strategies with LLM-driven news/catalyst analysis. Paper-first: nothing
trades live until a strategy is backtested, validated, and a pre-live audit is signed off.

> Status: **Phase 0 — scaffold.** Package skeleton and config profiles are in place;
> strategy, data, and execution logic are not yet implemented.

## Design principles

- **Survival over optimization** — risk limits and capital preservation come before returns.
- **Paper-first** — the `live` profile is hard-disabled (`enabled: false`) until the
  Phase D pre-live audit is complete.
- **Swing, not day-trading** — holding periods are chosen to avoid PDT constraints.
- **Capital-aware sizing** — position sizes scale to actual broker equity, not assumptions.

## Brokers & data

| Asset class | Broker | Data |
|-------------|--------|------|
| US stocks   | Alpaca | IEX feed only |
| Crypto      | Alpaca | Alpaca crypto |
| Forex       | OANDA  | OANDA |

## Layout

```
ea/
  brokers/      Alpaca + OANDA adapters
    alpaca/
    oanda/
  data/         market data ingestion & storage
  news/         news/catalyst pipeline (LLM analysis)
    fetchers/   per-asset-class feed fetchers
  strategies/   signal generation
  risk/         position sizing, exposure & loss limits
  execution/    order routing & fills
  monitoring/   health, alerts, reporting
config/
  live.yaml     restrictive live profile (disabled until pre-live audit)
  paper.yaml    base settings inherited by live profile
tests/
```

## Configuration

Profiles live in `config/`. The `live` profile inherits from `paper.yaml` and applies
tighter risk caps while ramping. Live trading stays disabled until the Phase D audit:

```yaml
profile: live
enabled: false   # do not change until pre-live audit is complete
```

Secrets are loaded from `.env` (and `.env.live` for live keys) — never committed.

## Development

```bash
# create / activate the virtual environment, then:
pytest
```

The Phase 0 smoke test verifies the package imports and exposes a version
(`ea.__version__ == "0.1.0"`).

## Risk controls (live profile)

- Daily loss limit: 1% · Weekly: 3%
- Per-position max: 3% · Per-trade risk: 0.25%
- Max concurrent positions: 10 (max 5 per asset class)
- Asset-class exposure caps: stocks 60% · crypto 30% · forex 30%
- Sector cap: 20% · Portfolio VaR cap: 1%
