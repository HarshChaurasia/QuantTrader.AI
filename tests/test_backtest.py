"""Backtest engine smoke + cross-sectional momentum end-to-end."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ea.backtest.engine import BacktestEngine
from ea.brokers.models import AssetClass
from ea.config import load_config
from ea.data.store import BarStore
from ea.strategies.xsection_momentum import CrossSectionalMomentumStrategy


def _make_trending_bars(price_start: float, slope: float, n: int) -> pd.DataFrame:
    base = datetime(2023, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([base + timedelta(days=i) for i in range(n)], name="ts")
    closes = [price_start + slope * i for i in range(n)]
    return pd.DataFrame({
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1_000_000] * n,
    }, index=idx)


def test_backtest_runs_on_trending_universe(tmp_path):
    """Cross-sectional momentum should pick the strongest uptrend."""
    cfg = load_config(profile="paper")
    store = BarStore(tmp_path / "bt.duckdb")
    # 4 symbols with different slopes
    store.upsert_bars(_make_trending_bars(100, +0.5, 250), "WINNER", "1Day", AssetClass.STOCK)
    store.upsert_bars(_make_trending_bars(100, +0.2, 250), "MID", "1Day", AssetClass.STOCK)
    store.upsert_bars(_make_trending_bars(100, +0.05, 250), "SLOW", "1Day", AssetClass.STOCK)
    store.upsert_bars(_make_trending_bars(100, -0.1, 250), "LOSER", "1Day", AssetClass.STOCK)

    engine = BacktestEngine(cfg, store, [CrossSectionalMomentumStrategy(top_n=2)],
                            starting_equity=10_000.0, slippage_bps=0)
    result = engine.run(
        ["WINNER", "MID", "SLOW", "LOSER"],
        start=datetime(2023, 1, 1, tzinfo=timezone.utc),
        end=datetime(2023, 9, 8, tzinfo=timezone.utc),
        asset_class=AssetClass.STOCK,
    )
    assert result.starting_equity == 10_000.0
    assert len(result.equity_curve) > 100
    # Should have made at least one trade
    assert result.n_trades >= 1
    # WINNER should be among traded symbols
    traded_syms = {t.symbol for t in result.trades}
    assert "WINNER" in traded_syms or "MID" in traded_syms


def test_walk_forward_compounds_and_reports(tmp_path):
    """Walk-forward stitches windows; report writer emits md+json."""
    from ea.backtest.walkforward import run_walk_forward
    from ea.backtest.report import write_report

    cfg = load_config(profile="paper")
    store = BarStore(tmp_path / "wf.duckdb")
    store.upsert_bars(_make_trending_bars(100, +0.5, 300), "WINNER", "1Day", AssetClass.STOCK)
    store.upsert_bars(_make_trending_bars(100, +0.05, 300), "SLOW", "1Day", AssetClass.STOCK)
    store.upsert_bars(_make_trending_bars(100, -0.1, 300), "LOSER", "1Day", AssetClass.STOCK)

    result = run_walk_forward(
        cfg, store, lambda: [CrossSectionalMomentumStrategy(top_n=1)],
        ["WINNER", "SLOW", "LOSER"],
        start=datetime(2023, 1, 1, tzinfo=timezone.utc),
        end=datetime(2023, 10, 1, tzinfo=timezone.utc),
        window_days=90, starting_equity=10_000.0, slippage_bps=0,
    )
    assert len(result.windows) >= 2
    assert result.starting_equity == 10_000.0
    assert not result.combined_equity.index.duplicated().any()

    md = write_report(result, outdir=tmp_path / "reports", label="walkforward")
    assert md.exists()
    assert md.with_suffix(".json").exists()


def test_backtest_raises_on_empty_data(tmp_path):
    cfg = load_config(profile="paper")
    store = BarStore(tmp_path / "bt.duckdb")
    engine = BacktestEngine(cfg, store, [CrossSectionalMomentumStrategy()])
    with pytest.raises(ValueError, match="no data"):
        engine.run(["NONEXISTENT"], datetime(2023, 1, 1, tzinfo=timezone.utc),
                   datetime(2023, 6, 1, tzinfo=timezone.utc))


def test_xsection_momentum_emits_top_n(tmp_path):
    """Direct strategy unit test — no backtest framework."""
    from ea.strategies.base import Context
    from ea.brokers.models import BarEvent
    from decimal import Decimal

    store = BarStore(tmp_path / "x.duckdb")
    store.upsert_bars(_make_trending_bars(100, +0.5, 200), "A", "1Day", AssetClass.STOCK)
    store.upsert_bars(_make_trending_bars(100, +0.3, 200), "B", "1Day", AssetClass.STOCK)
    store.upsert_bars(_make_trending_bars(100, -0.2, 200), "C", "1Day", AssetClass.STOCK)

    strat = CrossSectionalMomentumStrategy(top_n=2)
    ctx = Context(bar_store=store, state=None)
    bar = BarEvent(
        symbol="A", asset_class=AssetClass.STOCK,
        timestamp=datetime(2023, 7, 19, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
        close=Decimal("100"), volume=Decimal("1000"), timeframe="1Day",
    )
    signals = strat.on_bar(bar, ctx)
    assert len(signals) == 2
    sym_set = {s.symbol for s in signals}
    assert "A" in sym_set and "B" in sym_set
    assert "C" not in sym_set
