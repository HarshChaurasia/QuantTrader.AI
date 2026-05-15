"""Walk-forward harness.

The rule-based strategies here aren't parameter-fit, so "walk-forward" means
segmented out-of-sample runs: slice the period into consecutive windows, run
each as an independent backtest, and compound equity across them. This exposes
regime stability — a strategy that only works in one window shows up as a
window with a bad Sharpe instead of being averaged away by a good one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd

from ea.backtest.engine import BacktestEngine, BacktestResult
from ea.brokers.models import AssetClass
from ea.config import Config
from ea.data.store import BarStore
from ea.logging import logger
from ea.strategies.base import Strategy


@dataclass
class WalkForwardResult:
    windows: list[tuple[datetime, datetime, BacktestResult]]
    combined_equity: pd.Series
    starting_equity: float
    ending_equity: float
    sharpe: float
    max_drawdown_pct: float
    cagr: float
    n_trades: int
    win_rate: float

    def summary_lines(self) -> list[str]:
        lines = [
            f"Walk-forward : {len(self.windows)} windows",
            f"Period       : {self.combined_equity.index[0].date()} -> {self.combined_equity.index[-1].date()}",
            f"Starting eq  : ${self.starting_equity:,.2f}",
            f"Ending eq    : ${self.ending_equity:,.2f}",
            f"Total return : {(self.ending_equity / self.starting_equity - 1) * 100:+.2f}%",
            f"CAGR         : {self.cagr * 100:+.2f}%",
            f"Sharpe (all) : {self.sharpe:.2f}",
            f"Max DD       : {self.max_drawdown_pct:.2f}%",
            f"Trades       : {self.n_trades} (win rate {self.win_rate * 100:.1f}%)",
            "",
            "Per window:",
        ]
        for ws, we, r in self.windows:
            lines.append(
                f"  {ws.date()} -> {we.date()}: "
                f"{(r.ending_equity / r.starting_equity - 1) * 100:+.2f}% "
                f"Sharpe {r.sharpe:.2f} DD {r.max_drawdown_pct:.1f}% "
                f"({r.n_trades} trades)"
            )
        return lines


def run_walk_forward(
    config: Config,
    store: BarStore,
    strategy_factory,
    symbols: list[str],
    start: datetime,
    end: datetime,
    window_days: int = 90,
    step_days: int | None = None,
    starting_equity: float = 10_000.0,
    commission_bps: float = 0.0,
    slippage_bps: float = 5.0,
    asset_class: AssetClass = AssetClass.STOCK,
    timeframe: str = "1Day",
) -> WalkForwardResult:
    """Run consecutive backtests, compounding equity across windows.

    `strategy_factory` is a zero-arg callable returning a fresh list of
    Strategy instances — strategies hold per-run state, so each window needs
    its own. `step_days` defaults to `window_days` (non-overlapping).
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    step = step_days or window_days

    windows: list[tuple[datetime, datetime, BacktestResult]] = []
    curves: list[pd.Series] = []
    equity = starting_equity
    n_trades = 0
    n_wins = 0

    w_start = start
    while w_start < end:
        w_end = min(w_start + timedelta(days=window_days), end)
        engine = BacktestEngine(
            config, store, strategy_factory(),
            starting_equity=equity,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
        )
        try:
            result = engine.run(symbols, w_start, w_end, asset_class, timeframe)
        except ValueError as e:
            logger.warning("walk-forward: skipping window {} -> {}: {}", w_start.date(), w_end.date(), e)
            w_start = w_start + timedelta(days=step)
            continue

        windows.append((w_start, w_end, result))
        # Drop the first point of subsequent curves to avoid a duplicate
        # timestamp/flat step where windows abut.
        curve = result.equity_curve
        if curves:
            curve = curve.iloc[1:]
        curves.append(curve)
        equity = result.ending_equity
        n_trades += result.n_trades
        n_wins += sum(1 for t in result.trades if t.pnl > 0)
        w_start = w_start + timedelta(days=step)

    if not windows:
        raise ValueError("walk-forward: no window produced data — run `ea data backfill` first")

    combined = pd.concat(curves)
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()

    rets = combined.pct_change().dropna()
    ann = BacktestEngine._ANN_FACTOR.get(timeframe, 252)
    sharpe = float((rets.mean() / rets.std()) * (ann ** 0.5)) if len(rets) > 1 and rets.std() > 0 else 0.0
    rolling_max = combined.cummax()
    dd = (combined - rolling_max) / rolling_max
    max_dd_pct = abs(float(dd.min() * 100)) if len(dd) else 0.0
    days = (combined.index[-1] - combined.index[0]).days
    cagr = float((equity / starting_equity) ** (1 / (days / 365.25)) - 1) if days > 0 and starting_equity > 0 else 0.0

    return WalkForwardResult(
        windows=windows,
        combined_equity=combined,
        starting_equity=starting_equity,
        ending_equity=equity,
        sharpe=sharpe,
        max_drawdown_pct=max_dd_pct,
        cagr=cagr,
        n_trades=n_trades,
        win_rate=(n_wins / n_trades) if n_trades else 0.0,
    )
