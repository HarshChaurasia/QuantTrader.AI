# Risk management

> The single biggest determinant of long-run returns is not strategy quality — it's whether you survive your worst month.

## Layered risk framework

### Layer 1 — Per-position sizing

- **Vol-targeted risk parity.** Each position sized so its dollar-vol contribution is roughly equal: `size = (target_risk * equity) / (price * ATR_pct)`.
- **Per-symbol cap:** never more than 5% of equity in one symbol regardless of conviction.
- **Per-position stop:** every entry has a hard stop at `entry - K * ATR(14)` (long) or symmetric for short. K typically 1.0-1.5. No exceptions, no "I'll watch it" stops.

### Layer 2 — Portfolio risk budget

- **Daily VaR cap:** simulated 1-day 95% VaR across all positions ≤ 1.5% of equity. New entries blocked when this would push us over.
- **Concurrent positions:** max 15 across all asset classes; max 8 per asset class.
- **Sector cap (stocks):** max 25% in any one GICS sector.
- **Cluster cap (crypto):** max 30% in any one cluster (L1s, DeFi, memecoins, AI tokens, etc.) to avoid concentration when "crypto goes up" rotates between narratives.
- **Asset-class cap:** stocks ≤ 60%, crypto ≤ 30%, forex ≤ 30% of risk budget (not capital — risk budget).

### Layer 3 — Correlation overlay

- Maintain rolling 60-day correlation matrix.
- Reject new entries that would push portfolio average pairwise correlation above 0.4 (rough heuristic — refine after backtest).
- During risk-off events stocks/crypto correlation can spike from ~0.2 to ~0.7. Have explicit logic to scale all risk down when this happens.

### Layer 4 — Circuit breakers (hard halts)

| Trigger | Action |
|---------|--------|
| Daily loss > 2% of equity | Halt new entries, exits allowed. Manual review to resume. |
| Weekly loss > 5% of equity | Halt all activity until next week + manual review. |
| Single-position loss > 1.5% of equity in one day | Force-close that position; halt that strategy for 24h. |
| 5 consecutive losing trades in one strategy | Halve that strategy's risk budget; restore on 3 wins. |
| Broker disconnect > 60s | Cancel all pending orders, alert, attempt reconnect. |
| Account equity < 90% of trailing-30-day high | Reduce all position sizes by 50% until recovered. |
| LLM API failure during trading window | Continue with cached signals only; no new news-driven entries. |

These are mechanical, not discretionary. Code enforces them.

### Layer 5 — Regime filters

- **Equity regime:** VIX > 30 → halve stock allocation, no new long entries on momentum strategies.
- **Crypto regime:** BTC realized vol (30d) > 100% annualized → halve crypto allocation.
- **FX regime:** VIX > 25 → close all carry positions (carry crashes correlate with risk-off).

### Layer 6 — Operational

- **Idempotent orders:** every order has a `client_order_id` so a retry can never double-submit.
- **Position reconciliation:** every N seconds compare local position state vs broker; alert and halt on mismatch.
- **Crash safety:** state journaled to disk before any order submission. Recovery on restart replays journal and reconciles.
- **Time-based safety:** strategies that haven't received market data for > 5 minutes during market hours pause new entries.

## Sizing math (concrete)

For a position with:
- Equity: $10,000
- Target portfolio risk per trade: 0.5%
- Symbol price: $50
- 14-day ATR: $2 (4% of price)
- ATR multiple for stop: 1.5

```
risk_per_share = 1.5 * $2 = $3
position_risk_dollars = 0.005 * $10,000 = $50
shares = $50 / $3 = 16 shares
position_value = 16 * $50 = $800 (8% of equity, but only $50 at risk)
```

## What "risk management" is not

- **Not setting wider stops to "give it room."** Wider stops require smaller size. A loose stop with a normal size is a bigger bet than the strategy thinks it's making.
- **Not averaging down.** Adding to a losing position turns a 1×ATR loss into a 2× or 3× loss. No averaging in the risk model.
- **Not "I have conviction."** Conviction is already in the signal score. The risk manager doesn't reward conviction with extra size beyond the cap.
- **Not optimizing position size in backtest.** Use a single risk model across all strategies. Strategy-specific sizing optimization is a fast track to overfitting.

## Risk in backtests

The backtest engine MUST apply the same risk framework as live. A strategy that looks great with no risk overlay but only mediocre with the overlay applied — the overlay version is the truth.

## Implementation notes (as built)

- **Sizing input — `RiskManager._atr_pct`.** ATR(14) as a fraction of last
  close. Resolves bars by `signal.asset_class` (forex no longer misread as
  stock) and walks **1Day → 1Hour → 1Min** before the last-resort flat 2%
  default (`_ATR_DEFAULT_PCT`) — so a news-driven, never-streamed symbol still
  gets a real volatility estimate instead of the crude constant. NaN-safe.
- **Breakers enforced today:** daily-loss halt, weekly-loss halt, and
  per-strategy consecutive-loss budget shrink (`RiskManager.snapshot()`
  exposes `daily_halted` / `weekly_halted` / budget multipliers). VaR,
  correlation, sector/cluster and regime overlays remain design targets.
- **Operational alerts** (`ea/monitoring/alerts.py`) cover the breaker-trip,
  stream-disconnect, and stale/errored-order rows of the Layer-4/6 tables;
  they surface on the dashboard, deduped.
