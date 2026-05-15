"""S3 — Cross-sectional momentum.

Edge hypothesis: top-decile of past 6-month returns outperforms bottom-decile over
next 1-3 months. Time-tested factor (Jegadeesh & Titman 1993).

Implementation: on the first daily bar received per UTC date, scan all stock symbols
in the bar store, compute lookback returns, take top-N by return rank, emit LONG
signals. Skips short side for v1 simplicity (most retail accounts can't short
small-caps anyway).
"""
from __future__ import annotations

from datetime import date

from ea.brokers.models import AssetClass, BarEvent
from ea.logging import logger
from ea.strategies.base import Context, Signal, SignalSide, Strategy


class CrossSectionalMomentumStrategy(Strategy):
    name = "xsection_momentum"
    asset_classes = {AssetClass.STOCK, AssetClass.CRYPTO}

    def __init__(
        self,
        lookback_days: int = 126,   # ~6 months of trading days
        skip_recent_days: int = 21, # 1-month skip avoids short-term reversal
        top_n: int = 5,             # rebalance into top-N
        min_history_days: int = 150,
        horizon_days: int = 30,
        stop_atr: float = 1.5,
        take_atr: float = 3.0,
    ):
        self.lookback_days = lookback_days
        self.skip_recent_days = skip_recent_days
        self.top_n = top_n
        self.min_history = min_history_days
        self.horizon_days = horizon_days
        self.stop_atr = stop_atr
        self.take_atr = take_atr
        self._last_rebalance: date | None = None

    def on_bar(self, event: BarEvent, ctx: Context) -> list[Signal]:
        # Only act on daily bars (not the live minute stream)
        if event.timeframe != "1Day":
            return []
        today = event.timestamp.date()
        if self._last_rebalance == today:
            return []

        store = ctx.bar_store
        symbols = store.list_symbols(AssetClass.STOCK, "1Day")
        if not symbols:
            return []

        scored: list[tuple[str, float]] = []
        for sym in symbols:
            df = store.get_bars(sym, "1Day", AssetClass.STOCK)
            if df.empty or len(df) < self.min_history:
                continue
            # momentum: return from t-126 to t-21 (skip last month)
            try:
                start_idx = -(self.lookback_days + self.skip_recent_days)
                end_idx = -self.skip_recent_days
                if abs(start_idx) > len(df):
                    continue
                start_close = float(df["close"].iloc[start_idx])
                end_close = float(df["close"].iloc[end_idx])
                if start_close <= 0:
                    continue
                ret = (end_close - start_close) / start_close
                scored.append((sym, ret))
            except Exception:
                continue

        if len(scored) < self.top_n:
            self._last_rebalance = today
            return []

        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[: self.top_n]
        self._last_rebalance = today

        signals: list[Signal] = []
        for sym, ret in top:
            signals.append(Signal(
                strategy=self.name,
                symbol=sym,
                asset_class=AssetClass.STOCK,
                side=SignalSide.LONG,
                conviction=min(1.0, max(0.3, abs(ret))),
                horizon_days=self.horizon_days,
                stop_atr_mult=self.stop_atr,
                take_atr_mult=self.take_atr,
                rationale=f"6m momentum +{ret * 100:.1f}% (skip 1m)",
                metadata={"rank": top.index((sym, ret)) + 1, "lookback_return": ret},
            ))
        logger.info("xsection_momentum: rebalance {} -> top {}: {}",
                    today, self.top_n, [s.symbol for s in signals])
        return signals
