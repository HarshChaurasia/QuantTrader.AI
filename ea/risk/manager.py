"""Risk manager — pre-trade veto + sizing + circuit breakers.

Strategies emit Signals; the risk manager turns them into sized OrderRequests OR
rejects them with a reason. All limits per docs/RISK.md.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd

from ea.brokers.models import Account, AssetClass, OrderRequest, OrderSide, OrderType, Position
from ea.config import Config
from ea.eventbus import EventBus, get_bus
from ea.logging import logger
from ea.strategies.base import Signal, SignalSide


@dataclass
class RiskDecision:
    ok: bool
    order: OrderRequest | None
    reason: str
    signal: Signal


@dataclass
class CircuitState:
    daily_loss_pct: float = 0.0
    weekly_loss_pct: float = 0.0
    daily_halted: bool = False
    weekly_halted: bool = False
    consecutive_losses_by_strategy: dict[str, int] = field(default_factory=dict)
    strategy_budget_mult: dict[str, float] = field(default_factory=dict)  # 1.0 default; 0.5 if degraded
    starting_equity_today: float | None = None
    starting_equity_week: float | None = None


class RiskManager:
    def __init__(self, config: Config, bus: EventBus | None = None):
        self._config = config
        self._bus = bus or get_bus()
        self._state = CircuitState()
        self._recent_decisions: deque[RiskDecision] = deque(maxlen=200)

    @property
    def circuits(self) -> CircuitState:
        return self._state

    @property
    def recent(self) -> list[RiskDecision]:
        return list(self._recent_decisions)

    def update_equity(self, equity: float) -> None:
        s = self._state
        if s.starting_equity_today is None:
            s.starting_equity_today = equity
        if s.starting_equity_week is None:
            s.starting_equity_week = equity
        if s.starting_equity_today:
            s.daily_loss_pct = max(0.0, (s.starting_equity_today - equity) / s.starting_equity_today * 100)
        if s.starting_equity_week:
            s.weekly_loss_pct = max(0.0, (s.starting_equity_week - equity) / s.starting_equity_week * 100)
        r = self._config.profile.risk
        s.daily_halted = s.daily_loss_pct >= r.daily_loss_limit_pct
        s.weekly_halted = s.weekly_loss_pct >= r.weekly_loss_limit_pct

    def reset_daily(self, equity: float) -> None:
        """Anchor today's equity baseline to `equity` and clear the daily breaker.
        Called by the backtest engine on a new day, and by paper/live at session start."""
        self._state.starting_equity_today = equity
        self._state.daily_loss_pct = 0.0
        self._state.daily_halted = False

    def reset_weekly(self, equity: float) -> None:
        self._state.starting_equity_week = equity
        self._state.weekly_loss_pct = 0.0
        self._state.weekly_halted = False

    _ATR_DEFAULT_PCT = 0.02  # last-resort when no bars at any timeframe

    @staticmethod
    def _atr_pct_from_df(df) -> float | None:
        """ATR(14) as a fraction of last close, or None if df too thin."""
        if df is None or df.empty or len(df) < 15:
            return None
        hi, lo, cl = df["high"], df["low"], df["close"]
        tr = pd.concat(
            [hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1
        ).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        last_close = float(cl.iloc[-1])
        if last_close <= 0 or atr != atr:  # NaN guard
            return None
        return float(atr) / last_close

    def _atr_pct(self, bar_store: Any, symbol: str,
                 asset_class: AssetClass | None = None) -> float:
        """ATR% for vol sizing.

        Daily bars are best; if a symbol is news-driven but never streamed it
        may have no daily history, so fall back to 1Hour then 1Min bars
        (ATR-as-%-of-close is timeframe-tolerant for sizing) before the crude
        flat default. Asset class is honoured so forex symbols resolve to
        forex bars instead of being misread as stocks.
        """
        if asset_class is None:
            asset_class = AssetClass.CRYPTO if "/" in symbol else AssetClass.STOCK
        for tf in ("1Day", "1Hour", "1Min"):
            try:
                df = bar_store.get_bars(symbol, tf, asset_class)
            except Exception:
                continue
            val = self._atr_pct_from_df(df)
            if val is not None and val > 0:
                return val
        return self._ATR_DEFAULT_PCT

    def evaluate(
        self,
        signal: Signal,
        *,
        account: Account,
        positions: list[Position],
        bar_store: Any,
    ) -> RiskDecision:
        r = self._config.profile.risk

        # Update equity baseline
        self.update_equity(float(account.equity))

        # 1) Circuit breakers
        if self._state.daily_halted:
            return self._reject(signal, "daily loss circuit halted")
        if self._state.weekly_halted:
            return self._reject(signal, "weekly loss circuit halted")
        if account.trading_blocked:
            return self._reject(signal, "broker trading blocked")

        # 2) Position count caps
        if len(positions) >= r.max_concurrent_positions:
            return self._reject(signal, f"max concurrent positions reached ({r.max_concurrent_positions})")
        same_class = sum(1 for p in positions if p.asset_class == signal.asset_class)
        if same_class >= r.max_per_asset_class:
            return self._reject(signal, f"max per-asset-class ({r.max_per_asset_class})")

        # 3) Per-symbol cap (no doubling up)
        existing = next((p for p in positions if p.symbol == signal.symbol), None)
        if signal.side == SignalSide.FLAT:
            if existing is None:
                return self._reject(signal, "no position to flatten")
            # Close: opposite-side order of current qty
            qty = abs(float(existing.quantity))
            close_side = OrderSide.SELL if existing.quantity > 0 else OrderSide.BUY
            return self._accept(signal, self._build_order(
                signal, side=close_side, quantity=Decimal(str(qty)),
            ))

        if existing is not None:
            # Skip if same direction; ignore reversal for v1 simplicity
            if (existing.quantity > 0 and signal.side == SignalSide.LONG) or \
               (existing.quantity < 0 and signal.side == SignalSide.SHORT):
                return self._reject(signal, "already positioned in this direction")

        # 4) Position sizing — vol-targeted risk parity
        atr_pct = self._atr_pct(bar_store, signal.symbol, signal.asset_class)
        if atr_pct <= 0:
            return self._reject(signal, "invalid ATR")
        last_price = self._last_price(bar_store, signal.symbol)
        if last_price is None or last_price <= 0:
            return self._reject(signal, "no price data")

        risk_dollar = float(account.equity) * (r.per_trade_risk_pct / 100.0)
        risk_per_share = signal.stop_atr_mult * (atr_pct * last_price)
        if risk_per_share <= 0:
            return self._reject(signal, "zero risk-per-share")
        qty = risk_dollar / risk_per_share
        # Conviction scaling
        qty *= max(0.25, signal.conviction)
        # Per-position cap
        max_position_value = float(account.equity) * (r.per_position_max_pct / 100.0)
        if qty * last_price > max_position_value:
            qty = max_position_value / last_price

        if qty < 1.0:
            # Fractional for crypto; round to 4 decimals
            if signal.asset_class == AssetClass.CRYPTO:
                qty = max(qty, 0.0001)
            else:
                return self._reject(signal, f"computed qty < 1 (risk={risk_dollar:.2f}, atr={atr_pct:.3f})")

        # Round shares for stocks
        if signal.asset_class == AssetClass.STOCK:
            qty = float(int(qty))
        else:
            qty = round(qty, 4)

        side = OrderSide.BUY if signal.side == SignalSide.LONG else OrderSide.SELL
        return self._accept(signal, self._build_order(signal, side=side, quantity=Decimal(str(qty))))

    def _last_price(self, bar_store: Any, symbol: str) -> float | None:
        try:
            ac = AssetClass.CRYPTO if "/" in symbol else AssetClass.STOCK
            df = bar_store.get_bars(symbol, "1Day", ac)
            if df.empty:
                return None
            return float(df["close"].iloc[-1])
        except Exception:
            return None

    def _build_order(self, signal: Signal, *, side: OrderSide, quantity: Decimal) -> OrderRequest:
        return OrderRequest(
            symbol=signal.symbol,
            asset_class=signal.asset_class,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            client_order_id=f"ea-{signal.strategy}-{uuid.uuid4().hex[:10]}",
        )

    def _accept(self, signal: Signal, order: OrderRequest) -> RiskDecision:
        d = RiskDecision(ok=True, order=order, reason="ok", signal=signal)
        self._recent_decisions.appendleft(d)
        logger.info("risk: ACCEPT {} {} {} qty={}", signal.strategy, signal.symbol, signal.side.value, order.quantity)
        return d

    def _reject(self, signal: Signal, reason: str) -> RiskDecision:
        d = RiskDecision(ok=False, order=None, reason=reason, signal=signal)
        self._recent_decisions.appendleft(d)
        logger.info("risk: REJECT {} {} -- {}", signal.strategy, signal.symbol, reason)
        return d

    def record_fill_outcome(self, strategy: str, pnl: float) -> None:
        """Update consecutive-loss tracker; degrade strategy budget after 5 losses."""
        s = self._state
        if pnl < 0:
            s.consecutive_losses_by_strategy[strategy] = s.consecutive_losses_by_strategy.get(strategy, 0) + 1
            if s.consecutive_losses_by_strategy[strategy] >= 5:
                s.strategy_budget_mult[strategy] = 0.5
                logger.warning("strategy {} degraded to 50% budget (5 consecutive losses)", strategy)
        else:
            current = s.consecutive_losses_by_strategy.get(strategy, 0)
            if current >= 3 and s.strategy_budget_mult.get(strategy, 1.0) < 1.0:
                s.strategy_budget_mult[strategy] = 1.0
                logger.info("strategy {} budget restored", strategy)
            s.consecutive_losses_by_strategy[strategy] = 0

    def snapshot(self) -> dict:
        s = self._state
        return {
            "daily_loss_pct": round(s.daily_loss_pct, 3),
            "weekly_loss_pct": round(s.weekly_loss_pct, 3),
            "daily_halted": s.daily_halted,
            "weekly_halted": s.weekly_halted,
            "starting_equity_today": s.starting_equity_today,
            "consecutive_losses": dict(s.consecutive_losses_by_strategy),
            "budget_mult": dict(s.strategy_budget_mult),
        }
