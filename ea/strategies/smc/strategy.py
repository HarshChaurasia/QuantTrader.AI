"""SMCStrategy — emits Signals when price enters an unmitigated SMC zone.

Confluence rules:
- LONG only if BOS direction is bullish OR there's a recent bullish liquidity sweep,
  AND price is touching an unmitigated bullish FVG or OB.
- SHORT mirrored.

Stop is placed beyond the zone (outside by 0.5 × zone height); target is risk_reward
× risk distance. Default RR = 2.0.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from ea.brokers.models import AssetClass, BarEvent
from ea.logging import logger
from ea.strategies.base import Context, Signal, SignalSide, Strategy
from ea.strategies.smc.patterns import (
    Zone, detect_bos, detect_fvgs, detect_liquidity_sweeps, detect_order_blocks,
)


def _best_zone(zones: list[Zone], current_price: float, side: str) -> Optional[Zone]:
    """Return closest unmitigated zone to current price on the given side.

    Bullish (demand): a zone at or below price that price can retrace *into*.
    We keep any zone whose low is at/below current price (price hasn't broken
    through it yet) — this includes zones that currently *contain* price, so
    the in-zone check downstream can actually fire. Pick the highest such zone
    (nearest from below / containing). Bearish is mirrored.
    """
    candidates = [z for z in zones if z.side == side and not z.mitigated]
    if not candidates:
        return None
    if side == "bullish":
        relevant = [z for z in candidates if z.low <= current_price]
        if not relevant:
            return None
        return max(relevant, key=lambda z: z.high)  # nearest from below / containing
    else:
        relevant = [z for z in candidates if z.high >= current_price]
        if not relevant:
            return None
        return min(relevant, key=lambda z: z.low)  # nearest from above / containing


def evaluate_setup(df: pd.DataFrame, risk_reward: float = 2.0) -> Optional[dict]:
    """Pure-data SMC analysis. Returns a setup dict or None.

    Fields: side, zone_kind, entry_low, entry_high, stop, target, distance_pct,
    status (in_zone | approaching | watching), confluence (list[str]).
    """
    if df is None or len(df) < 30:
        return None

    current_price = float(df["close"].iloc[-1])

    fvgs = detect_fvgs(df, lookback_bars=80)
    obs = detect_order_blocks(df, lookback_bars=80)
    liqs = detect_liquidity_sweeps(df, lookback_bars=40)
    bos = detect_bos(df)

    # Confluence assembly per side
    sides_eval: list[tuple[str, list[str]]] = []
    bull_conf, bear_conf = [], []
    if bos is not None and bos.side == "bullish":
        bull_conf.append("BOS↑")
    if bos is not None and bos.side == "bearish":
        bear_conf.append("BOS↓")
    bull_liq = any(z.side == "bullish" for z in liqs)
    bear_liq = any(z.side == "bearish" for z in liqs)
    if bull_liq:
        bull_conf.append("LiqSweep↑")
    if bear_liq:
        bear_conf.append("LiqSweep↓")
    if bull_conf:
        sides_eval.append(("bullish", bull_conf))
    if bear_conf:
        sides_eval.append(("bearish", bear_conf))

    if not sides_eval:
        return None

    # Pick the side with more confluence; for ties prefer bullish if BOS bullish
    sides_eval.sort(key=lambda x: len(x[1]), reverse=True)
    side, confluence = sides_eval[0]

    # Find a target zone (FVG or OB) on the chosen side
    all_zones = fvgs + obs
    zone = _best_zone(all_zones, current_price, side)
    if zone is None:
        return None
    confluence.append(f"{zone.kind}↑" if side == "bullish" else f"{zone.kind}↓")

    # Enforce a minimum zone width: raw FVGs/OBs can be a few cents wide, which
    # makes "price inside the zone" practically impossible. Pad thin zones to at
    # least MIN_ZONE_PCT of price, centered on the original zone midpoint.
    MIN_ZONE_PCT = 0.004  # 0.4% of price
    z_mid = (zone.low + zone.high) / 2
    min_half = current_price * MIN_ZONE_PCT / 2
    z_low = min(zone.low, z_mid - min_half)
    z_high = max(zone.high, z_mid + min_half)
    height = (z_high - z_low) or (current_price * 0.005)

    if side == "bullish":
        entry_low, entry_high = z_low, z_high
        stop = z_low - height * 0.5
        entry_mid = (entry_low + entry_high) / 2
        risk_at_mid = entry_mid - stop
        target = entry_mid + risk_at_mid * risk_reward
        in_zone = entry_low <= current_price <= entry_high
        distance_pct = (current_price - entry_high) / current_price * 100
    else:
        entry_low, entry_high = z_low, z_high
        stop = z_high + height * 0.5
        entry_mid = (entry_low + entry_high) / 2
        risk_at_mid = stop - entry_mid
        target = entry_mid - risk_at_mid * risk_reward
        in_zone = entry_low <= current_price <= entry_high
        distance_pct = (entry_low - current_price) / current_price * 100

    # Proximity bands widened: SMC swing entries trigger on a retrace that can
    # take a while, so treat anything within 4% as "approaching".
    if in_zone:
        status = "in_zone"
    elif abs(distance_pct) < 4.0:
        status = "approaching"
    else:
        status = "watching"

    return {
        "side": side,
        "zone_kind": zone.kind,
        "zone_ts": zone.ts.isoformat() if hasattr(zone.ts, "isoformat") else str(zone.ts),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop": stop,
        "target": target,
        "current_price": current_price,
        "risk_reward": risk_reward,
        "distance_pct": distance_pct,
        "status": status,
        "confluence": confluence,
    }


class SMCStrategy(Strategy):
    name = "smc"
    asset_classes = {AssetClass.STOCK, AssetClass.CRYPTO, AssetClass.FOREX}

    def __init__(self, risk_reward: float = 2.0, horizon_days: int = 7, timeframe: str = "1Hour"):
        self.risk_reward = risk_reward
        self.horizon_days = horizon_days
        self.timeframe = timeframe
        self._last_signal_ts: dict[str, datetime] = {}

    def on_bar(self, event: BarEvent, ctx: Context) -> list[Signal]:
        if event.timeframe != self.timeframe:
            return []

        store = ctx.bar_store
        df = store.get_bars(event.symbol, self.timeframe, event.asset_class)
        if df is None or len(df) < 30:
            return []

        setup = evaluate_setup(df, risk_reward=self.risk_reward)
        if setup is None or setup["status"] != "in_zone":
            return []

        # Cooldown: don't re-emit on the same symbol within 24h
        last = self._last_signal_ts.get(event.symbol)
        cur_ts = event.timestamp
        if last is not None and (cur_ts - last).total_seconds() < 86_400:
            return []
        self._last_signal_ts[event.symbol] = cur_ts

        side = SignalSide.LONG if setup["side"] == "bullish" else SignalSide.SHORT
        # Stop ATR mult derived from zone-relative stop
        # Risk manager will recompute based on its ATR; conviction reflects confluence count
        conviction = min(1.0, 0.4 + 0.15 * len(setup["confluence"]))
        signal = Signal(
            strategy=self.name,
            symbol=event.symbol,
            asset_class=event.asset_class,
            side=side,
            conviction=conviction,
            horizon_days=self.horizon_days,
            stop_atr_mult=1.0,
            take_atr_mult=self.risk_reward,
            rationale=f"{setup['zone_kind']} {setup['side']} | " + " ".join(setup["confluence"]),
            metadata=setup,
        )
        logger.info("smc: SIGNAL {} {} via {}", event.symbol, side.value, setup["confluence"])
        return [signal]
