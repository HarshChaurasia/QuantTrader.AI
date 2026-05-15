"""SMCScalpStrategy — the SMC strategy run on 1-minute bars for scalping.

Deliberately reuses `evaluate_setup` from the swing SMC strategy **unchanged**:
same strict confluence (BOS or LiqSweep + FVG/OB + price in-zone). The only
differences vs the swing variant are operational, not signal-quality:

- 1Min timeframe instead of 1Hour
- crypto + forex only (no PDT; stocks would trip pattern-day-trader rules)
- short re-entry cooldown (minutes, not 24h) so it can actually scalp
- tighter R:R and a 1-day max horizon (zone stop/target exits dominate intraday)

Keeping confluence strict is a standing project decision — scalp frequency is
achieved by lowering the timeframe, never by loosening the gates.
"""
from __future__ import annotations

from ea.brokers.models import AssetClass
from ea.strategies.smc.strategy import SMCStrategy


class SMCScalpStrategy(SMCStrategy):
    name = "smc_scalp"
    asset_classes = {AssetClass.CRYPTO, AssetClass.FOREX}

    def __init__(
        self,
        risk_reward: float = 1.5,
        horizon_days: int = 1,
        timeframe: str = "1Min",
        cooldown_s: float = 300.0,
    ):
        super().__init__(
            risk_reward=risk_reward,
            horizon_days=horizon_days,
            timeframe=timeframe,
            cooldown_s=cooldown_s,
        )
