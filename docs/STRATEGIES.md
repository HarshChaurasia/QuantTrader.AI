# Strategy candidates

Each strategy below has a stated edge hypothesis, holding period, evidence base, and an LLM-augmentation role. They're candidates — final inclusion depends on backtest results.

## Stocks

### S1 — Post-Earnings Announcement Drift (PEAD)
- **Edge hypothesis:** Stocks that beat earnings + guidance drift up over 1-60 days; misses drift down. Post-earnings drift is one of the longest-documented anomalies (Bernard & Thomas 1989; still detectable in modern data with weakened but non-zero edge).
- **Entry:** day after earnings, on stocks that (a) beat consensus EPS and (b) Claude-scored earnings call as net-positive (guidance, tone, defensiveness in Q&A).
- **Exit:** time stop 30 days, or 2×ATR profit target, or hard stop 1×ATR.
- **LLM role:** Claude reads the call transcript and scores `(guidance_direction, management_tone, analyst_pushback, surprise_magnitude_qualitative)`. Pure EPS surprise alone is over-mined; LLM-scored qualitative beats add the missing signal.
- **Risk:** crowded trade in mega-caps, less crowded in mid-caps. Filter to $1B-$50B market cap initially.

### S2 — Catalyst-driven news momentum
- **Edge hypothesis:** Material catalysts (FDA decisions, M&A announcements, large contract wins, guidance raises outside earnings) cause 3-10 day drift as the buyside repositions.
- **Entry:** within 1 trading day of LLM-flagged material catalyst, in direction of catalyst, on names with adequate liquidity.
- **Exit:** time stop 7 days, profit target 1.5×ATR, hard stop 1×ATR.
- **LLM role:** scoring `(catalyst_type, materiality, durability, expected_direction)` — separating "Apple announces partnership with bakery" from "Apple announces partnership with OpenAI."
- **Risk:** false catalysts, gap risk, over-reaction reversals. Avoid binary biotech events (FDA approvals are often priced in or violently reverse).

### S3 — Cross-sectional momentum
- **Edge hypothesis:** Top decile of past 6-12 month returns outperforms bottom decile over next 1-3 months. Time-tested factor (Jegadeesh & Titman 1993; survives in most regimes ex-2009-style reversals).
- **Entry:** monthly rebalance into top-decile names, equal-weight or vol-targeted, optionally short bottom decile if account permits.
- **Exit:** monthly rebalance.
- **LLM role:** none directly — this is a pure factor strategy. Acts as a stable baseline that's uncorrelated with news/event strategies.
- **Risk:** momentum crashes (sharp regime reversals). Mitigation: regime filter (turn off when realized vol of momentum spread > threshold).

## Crypto

### C1 — Catalyst news momentum
- **Edge hypothesis:** Crypto reacts to news (token unlocks, exchange listings, protocol upgrades, regulatory rulings) more than equities because retail-driven and 24/7. Multi-day drift on material news.
- **Entry:** Claude-flagged material news on top-50 alts.
- **Exit:** 5-day time stop, 2×ATR target, 1×ATR stop.
- **LLM role:** filter signal vs noise (CoinDesk publishes hundreds of articles/day, most don't move price).

### C2 — Cross-sectional crypto momentum
- **Edge hypothesis:** Same momentum effect as equities, often stronger in crypto due to retail flows.
- **Entry:** weekly rebalance, top-N alts by 30-day return, equal-weight, USD-funded.
- **Exit:** weekly rebalance.
- **Risk:** correlation spikes during BTC drawdowns.

### C3 — BTC dominance regime filter
- Not a standalone strategy — overlay that scales down crypto exposure when BTC dominance is rising sharply (alts bleed) or VIX equivalent (DVOL) is extreme.

## Forex

### F1 — Carry trade
- **Edge hypothesis:** Long high-yielding currencies vs short low-yielding currencies has produced positive long-run returns despite occasional sharp reversals. Classic FX strategy (Lustig & Verdelhan).
- **Entry:** monthly rebalance — sort G10 by short-term policy rate, long top 3, short bottom 3.
- **Exit:** monthly rebalance, with VIX > 25 → close all (carry crashes correlate with risk-off).
- **LLM role:** none — pure rate-differential strategy.

### F2 — Central-bank-statement momentum
- **Edge hypothesis:** Hawkish/dovish surprises in Fed/ECB/BoJ statements cause multi-day FX moves as positioning unwinds.
- **Entry:** within 30 minutes of statement release, Claude-parsed for `(direction, surprise_magnitude, forward_guidance_change)` vs prior expectations.
- **Exit:** 3-day time stop, ATR-based.
- **LLM role:** central — parsing statement nuance is exactly what Claude is good at and what retail traders typically can't do well at speed.
- **Risk:** initial price reaction is fast — accept later entries (we're swing, not racing the spike).

### F3 — Trend on majors
- **Edge hypothesis:** Persistent macro themes drive multi-week FX trends.
- **Entry:** Donchian-channel breakouts on EURUSD, USDJPY, GBPUSD, AUDUSD with ATR-confirmed momentum.
- **Exit:** trailing stop at N×ATR.
- **Risk:** chop in range-bound regimes; pair with regime filter.

## Strategy ensemble principle

Run all selected strategies concurrently with **risk-budget allocation, not equal-weight**. Each strategy gets a vol budget; total portfolio risk capped centrally. Strategies that draw down get budget reduced automatically (risk-budget shrinks 50% if 30-day Sharpe goes negative).

This means a single failing strategy can't sink the portfolio, and the system survives long enough for the working strategies to compound.

## Implemented strategies (as built — reality vs. candidates above)

The candidates above are the design menu. What actually runs in parallel today
(registered in `StrategyRunner`, validated by the event-driven backtest):

| Name | Maps to | Asset classes | Timeframe | Notes |
|---|---|---|---|---|
| `news_momentum` | S2 | stock, crypto | event | LLM-scored catalyst drift |
| `xsection_momentum` | S3 / C2 | stock, crypto | 1Day | pure factor baseline |
| `smc` | (new) | stock, crypto, forex | 1Hour | SMC swing: BOS/LiqSweep + FVG/OB, in-zone, strict confluence, 24h re-entry cooldown |
| `smc_scalp` | (new) | crypto, forex | 1Min | **same `evaluate_setup` as `smc`, unchanged** — only timeframe/cooldown/RR differ |

SMC (Smart Money Concepts) was not in the original candidate list; it was added
as a structure-based technical strategy. Confluence is deliberately strict
(user decision) — sparse setups are correct behaviour, not a bug. `smc_scalp`
gets scalp frequency purely from the 1Min timeframe; it does **not** loosen the
gates. Forex scalp is signal-only (no live execution until OANDA / Phase C).

PEAD (S1), the crypto C1/C3 strategies, and FX F1–F3 remain unimplemented (see
`docs/REMAINING.md`).

## Strategies explicitly NOT in scope

- **Mean-reversion intraday scalping.** Out of scope — competes with HFT, fees
  dominate. *Note:* `smc_scalp` is **not** this — it is structure-based (SMC
  zones + confluence), momentum-direction, on liquid crypto/FX only, added at
  explicit user request. The exclusion still holds for naive RSI/MACD
  mean-reversion scalping.
- **Options strategies.** Phase D+ at earliest. Adds another dimension of risk that's not productive to manage in v1.
- **Pure technical-only (no catalyst/factor):** RSI/MACD/chart patterns alone don't have edge after costs. We use technicals for *exits and risk sizing*, not entries.
- **Crypto perp basis arb / funding rate harvest.** Real edge but requires venue accounts (Binance/Bybit) and active hedge management. Defer.
