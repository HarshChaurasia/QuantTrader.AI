"""Minimal event-driven backtest engine.

Replays daily bars (from store) through the same Strategy → Risk → Order code path
as live, with a simulated broker that fills at next-day open + cost model.

Scope: daily bars + technical strategies. News-driven backtests need cached LLM
analyses paired with their bar dates; deferred to a future iteration.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

import pandas as pd

from ea.brokers.models import (
    Account, AssetClass, BarEvent, Order, OrderRequest, OrderSide, OrderStatus,
    OrderType, Position,
)
from ea.config import Config
from ea.data.store import BarStore
from ea.logging import logger
from ea.risk.manager import RiskManager
from ea.strategies.base import Context, Signal, SignalSide, Strategy


@dataclass
class Trade:
    symbol: str
    side: OrderSide
    quantity: Decimal
    entry_price: float
    entry_date: datetime
    exit_price: float | None = None
    exit_date: datetime | None = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    strategy: str = ""
    horizon_days: int = 0


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade]
    starting_equity: float
    ending_equity: float
    n_trades: int
    win_rate: float
    avg_pnl_pct: float
    sharpe: float
    max_drawdown_pct: float
    cagr: float
    by_strategy: dict[str, dict]
    config_summary: dict

    def summary_lines(self) -> list[str]:
        return [
            f"Period       : {self.equity_curve.index[0].date()} -> {self.equity_curve.index[-1].date()}",
            f"Starting eq  : ${self.starting_equity:,.2f}",
            f"Ending eq    : ${self.ending_equity:,.2f}",
            f"Total return : {(self.ending_equity / self.starting_equity - 1) * 100:+.2f}%",
            f"CAGR         : {self.cagr * 100:+.2f}%",
            f"Sharpe       : {self.sharpe:.2f}",
            f"Max DD       : {self.max_drawdown_pct:.2f}%",
            f"Trades       : {self.n_trades} (win rate {self.win_rate * 100:.1f}%)",
            f"Avg PnL/trade: {self.avg_pnl_pct * 100:+.3f}%",
        ]


class _SimAccount:
    """In-memory account state for the simulator."""

    def __init__(self, starting_equity: float):
        self.cash = starting_equity
        self.equity = starting_equity
        self.positions: dict[str, Position] = {}
        self.entry_dates: dict[str, datetime] = {}
        self.entry_strategies: dict[str, str] = {}
        self.entry_horizons: dict[str, int] = {}

    def to_account(self) -> Account:
        return Account(
            account_id="BACKTEST", currency="USD",
            cash=Decimal(str(self.cash)),
            equity=Decimal(str(self.equity)),
            buying_power=Decimal(str(self.equity * 2)),
            portfolio_value=Decimal(str(self.equity)),
        )

    def position_list(self) -> list[Position]:
        return list(self.positions.values())

    def mark_to_market(self, prices: dict[str, float]) -> None:
        total_value = self.cash
        for sym, p in self.positions.items():
            px = prices.get(sym)
            if px is None:
                px = float(p.current_price or p.avg_entry_price)
            mv = float(p.quantity) * px
            total_value += mv
            self.positions[sym] = Position(
                symbol=sym, asset_class=p.asset_class, quantity=p.quantity,
                avg_entry_price=p.avg_entry_price, current_price=Decimal(str(px)),
                market_value=Decimal(str(mv)),
                unrealized_pl=Decimal(str(mv - float(p.avg_entry_price) * float(p.quantity))),
                unrealized_pl_pct=(px / float(p.avg_entry_price) - 1) if float(p.avg_entry_price) else 0.0,
                cost_basis=Decimal(str(float(p.avg_entry_price) * float(p.quantity))),
            )
        self.equity = total_value


class BacktestEngine:
    """Daily-bar backtest. Limit orders treated as marketable; market orders fill at next bar open."""

    def __init__(
        self,
        config: Config,
        store: BarStore,
        strategies: Iterable[Strategy],
        starting_equity: float = 10_000.0,
        commission_bps: float = 0.0,        # Alpaca stocks free; set for crypto
        slippage_bps: float = 5.0,           # 0.05% one-way slippage estimate
    ):
        self._config = config
        self._store = store
        self._strategies = list(strategies)
        self._starting_equity = starting_equity
        self._commission_bps = commission_bps
        self._slippage_bps = slippage_bps

    def run(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        asset_class: AssetClass = AssetClass.STOCK,
    ) -> BacktestResult:
        # Load all bars for all symbols once, indexed by date for fast lookup
        per_symbol: dict[str, pd.DataFrame] = {}
        all_dates: set[datetime] = set()
        for sym in symbols:
            df = self._store.get_bars(sym, "1Day", asset_class, start=start, end=end)
            if df.empty:
                logger.warning("backtest: no bars for {}", sym)
                continue
            per_symbol[sym] = df
            all_dates.update(df.index.tolist())

        if not all_dates:
            raise ValueError("backtest: no data for any requested symbol — run `ea data backfill` first")

        timeline = sorted(all_dates)
        sim = _SimAccount(self._starting_equity)
        risk = RiskManager(self._config)
        ctx = Context(bar_store=self._store, state=None)
        risk.reset_daily(self._starting_equity)
        risk.reset_weekly(self._starting_equity)

        equity_history: list[tuple[datetime, float]] = []
        trades: list[Trade] = []
        pending_orders: list[tuple[OrderRequest, str, int]] = []  # (order, strategy, horizon)
        prev_date = None
        prev_iso_week = None

        for ts in timeline:
            cur_date = ts.date() if hasattr(ts, "date") else ts
            if prev_date is not None and cur_date != prev_date:
                risk.reset_daily(sim.equity)
            iso_week = cur_date.isocalendar()[:2]  # (year, week)
            if prev_iso_week is not None and iso_week != prev_iso_week:
                risk.reset_weekly(sim.equity)
            prev_date, prev_iso_week = cur_date, iso_week
            day_prices: dict[str, float] = {}
            day_bars: list[BarEvent] = []
            for sym, df in per_symbol.items():
                if ts not in df.index:
                    continue
                row = df.loc[ts]
                day_prices[sym] = float(row["close"])
                day_bars.append(BarEvent(
                    symbol=sym,
                    asset_class=AssetClass.CRYPTO if "/" in sym else AssetClass.STOCK,
                    timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
                    volume=Decimal(str(row["volume"])), timeframe="1Day",
                ))

            # Fill pending orders at this bar's open
            still_pending: list[tuple[OrderRequest, str, int]] = []
            for order, strat_name, horizon in pending_orders:
                if order.symbol not in day_prices:
                    still_pending.append((order, strat_name, horizon))
                    continue
                fill_open = float(per_symbol[order.symbol].loc[ts]["open"])
                slip = self._slippage_bps / 10_000.0
                fill_price = fill_open * (1 + slip if order.side == OrderSide.BUY else 1 - slip)
                self._execute_fill(sim, order, fill_price, ts, strat_name, horizon, trades)
            pending_orders = still_pending

            # Mark-to-market
            sim.mark_to_market(day_prices)
            equity_history.append((ts, sim.equity))

            # Time-stop exits
            to_close: list[str] = []
            for sym, entry_date in list(sim.entry_dates.items()):
                horizon = sim.entry_horizons.get(sym, 30)
                age = (ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts) - entry_date
                if age.days >= horizon and sym in day_prices:
                    to_close.append(sym)
            for sym in to_close:
                self._close_position(sim, sym, day_prices[sym], ts, trades)

            # Generate signals
            account = sim.to_account()
            risk.update_equity(sim.equity)
            for ev in day_bars:
                for strat in self._strategies:
                    try:
                        for sig in strat.on_bar(ev, ctx):
                            d = risk.evaluate(sig, account=account, positions=sim.position_list(), bar_store=self._store)
                            if d.ok and d.order is not None:
                                pending_orders.append((d.order, sig.strategy, sig.horizon_days))
                    except Exception as e:
                        logger.warning("backtest strategy error: {}", e)

        # Close remaining positions at last close
        last_ts = timeline[-1]
        last_prices = {sym: float(df.loc[last_ts]["close"]) for sym, df in per_symbol.items() if last_ts in df.index}
        for sym in list(sim.positions.keys()):
            if sym in last_prices:
                self._close_position(sim, sym, last_prices[sym], last_ts, trades)
        sim.mark_to_market(last_prices)
        equity_history.append((last_ts, sim.equity))

        return self._summarize(equity_history, trades)

    def _execute_fill(self, sim: _SimAccount, order: OrderRequest, price: float, ts,
                      strat: str, horizon: int, trades: list[Trade]) -> None:
        qty = float(order.quantity)
        cost = qty * price
        commission = cost * (self._commission_bps / 10_000.0)
        if order.side == OrderSide.BUY:
            if sim.cash < cost + commission:
                return  # insufficient cash
            sim.cash -= (cost + commission)
            sim.positions[order.symbol] = Position(
                symbol=order.symbol,
                asset_class=AssetClass.CRYPTO if "/" in order.symbol else AssetClass.STOCK,
                quantity=Decimal(str(qty)),
                avg_entry_price=Decimal(str(price)),
            )
            sim.entry_dates[order.symbol] = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            sim.entry_strategies[order.symbol] = strat
            sim.entry_horizons[order.symbol] = horizon
        else:  # SELL — close long
            existing = sim.positions.get(order.symbol)
            if existing is None:
                return
            self._close_position(sim, order.symbol, price, ts, trades)

    def _close_position(self, sim: _SimAccount, symbol: str, price: float, ts, trades: list[Trade]) -> None:
        p = sim.positions.get(symbol)
        if p is None:
            return
        qty = float(p.quantity)
        proceeds = qty * price
        commission = proceeds * (self._commission_bps / 10_000.0)
        sim.cash += (proceeds - commission)
        entry_price = float(p.avg_entry_price)
        pnl = (price - entry_price) * qty - commission
        pnl_pct = (price / entry_price - 1) if entry_price else 0.0

        trades.append(Trade(
            symbol=symbol, side=OrderSide.BUY, quantity=Decimal(str(qty)),
            entry_price=entry_price,
            entry_date=sim.entry_dates.get(symbol, ts),
            exit_price=price,
            exit_date=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            pnl=pnl, pnl_pct=pnl_pct,
            strategy=sim.entry_strategies.get(symbol, "?"),
            horizon_days=sim.entry_horizons.get(symbol, 0),
        ))

        del sim.positions[symbol]
        sim.entry_dates.pop(symbol, None)
        sim.entry_strategies.pop(symbol, None)
        sim.entry_horizons.pop(symbol, None)

    def _summarize(self, equity_history, trades) -> BacktestResult:
        idx = [t for t, _ in equity_history]
        eq = [v for _, v in equity_history]
        curve = pd.Series(eq, index=pd.DatetimeIndex(idx))

        starting = float(curve.iloc[0])
        ending = float(curve.iloc[-1])

        # Sharpe (daily returns, no risk-free rate, annualized 252)
        rets = curve.pct_change().dropna()
        if len(rets) > 1 and rets.std() > 0:
            sharpe = float((rets.mean() / rets.std()) * (252 ** 0.5))
        else:
            sharpe = 0.0

        # Max drawdown
        rolling_max = curve.cummax()
        dd_series = (curve - rolling_max) / rolling_max
        max_dd_pct = float(dd_series.min() * 100) if len(dd_series) else 0.0

        # CAGR
        days = (curve.index[-1] - curve.index[0]).days
        if days > 0 and starting > 0:
            years = days / 365.25
            cagr = float((ending / starting) ** (1 / years) - 1)
        else:
            cagr = 0.0

        n = len(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = (wins / n) if n else 0.0
        avg_pnl_pct = (sum(t.pnl_pct for t in trades) / n) if n else 0.0

        by_strat: dict[str, dict] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
        for t in trades:
            by_strat[t.strategy]["trades"] += 1
            by_strat[t.strategy]["pnl"] += t.pnl
            if t.pnl > 0:
                by_strat[t.strategy]["wins"] += 1

        return BacktestResult(
            equity_curve=curve,
            trades=trades,
            starting_equity=starting,
            ending_equity=ending,
            n_trades=n,
            win_rate=win_rate,
            avg_pnl_pct=avg_pnl_pct,
            sharpe=sharpe,
            max_drawdown_pct=abs(max_dd_pct),
            cagr=cagr,
            by_strategy=dict(by_strat),
            config_summary={
                "starting_equity": starting,
                "commission_bps": self._commission_bps,
                "slippage_bps": self._slippage_bps,
                "strategies": [s.name for s in self._strategies],
            },
        )
