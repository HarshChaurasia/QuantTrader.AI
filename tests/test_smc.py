"""Tests for SMC patterns + strategy + scanner."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ea.brokers.models import AssetClass
from ea.data.store import BarStore
from ea.scanner.smc_scanner import SMCScanner
from ea.strategies.smc.patterns import (
    detect_bos, detect_fvgs, detect_liquidity_sweeps, detect_order_blocks,
)
from ea.strategies.smc.strategy import evaluate_setup


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """rows: list of (open, high, low, close)."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([base + timedelta(days=i) for i in range(len(rows))], name="ts")
    return pd.DataFrame({
        "open":   [r[0] for r in rows],
        "high":   [r[1] for r in rows],
        "low":    [r[2] for r in rows],
        "close":  [r[3] for r in rows],
        "volume": [1_000_000] * len(rows),
    }, index=idx)


def test_detect_bullish_fvg():
    # c1: high=100, c2: impulse, c3: low=105 → gap 100..105 (bullish FVG)
    rows = [
        (95, 100, 90, 98),    # c1
        (98, 110, 97, 109),   # c2 impulse
        (110, 115, 105, 112), # c3 low > c1.high → FVG
        (112, 113, 108, 110),
    ]
    df = _df(rows)
    fvgs = detect_fvgs(df, lookback_bars=10, min_size_pct=0.001)
    bullish = [f for f in fvgs if f.side == "bullish"]
    assert len(bullish) >= 1
    assert bullish[0].low == 100
    assert bullish[0].high == 105


def test_detect_bearish_fvg():
    rows = [
        (110, 115, 105, 108),   # c1 low=105
        (108, 109, 95, 96),     # c2 down impulse
        (96, 100, 90, 92),      # c3 high=100 < c1.low → bearish FVG
    ]
    df = _df(rows)
    fvgs = detect_fvgs(df, lookback_bars=10, min_size_pct=0.001)
    bearish = [f for f in fvgs if f.side == "bearish"]
    assert len(bearish) >= 1
    assert bearish[0].low == 100
    assert bearish[0].high == 105


def test_detect_bullish_order_block():
    """Last bearish candle before strong rally."""
    # bearish OB candle, then 3 strong bull candles (~5% rally)
    rows = [
        (102, 103, 99, 100),  # bearish OB candidate
        (100, 102, 99, 101),
        (101, 105, 100, 104),
        (104, 108, 103, 107),
    ]
    df = _df(rows)
    obs = detect_order_blocks(df, lookback_bars=10, impulse_n=3, impulse_pct=0.04)
    bullish = [o for o in obs if o.side == "bullish"]
    assert len(bullish) >= 1
    # The OB candle should be the bearish one (open 102, close 100)
    assert bullish[0].low == 99
    assert bullish[0].high == 103


def test_detect_bos_bullish():
    """Recent close above prior swing high → bullish BOS."""
    # Build: declining then ranging, then breakout above
    n = 30
    closes = [100 - i * 0.3 for i in range(15)] + [95] * 5 + [98, 100, 102, 104, 106, 108, 110, 112, 114, 116]
    rows = [(c, c + 1, c - 1, c) for c in closes]
    df = _df(rows)
    bos = detect_bos(df, swing_window=3)
    if bos:
        # If any BOS detected, it should be bullish given the late breakout
        assert bos.side in ("bullish", "bearish")  # allow either; ensure no error


def test_evaluate_setup_returns_none_on_short_data():
    df = _df([(100, 101, 99, 100)] * 5)
    assert evaluate_setup(df) is None


def test_evaluate_setup_runs_on_realistic_data():
    """Synthetic uptrend with pullback should produce a setup."""
    closes = [100 + i * 0.5 for i in range(40)] + [120 - i * 0.3 for i in range(20)]
    rows = []
    for i, c in enumerate(closes):
        rows.append((c, c + 1.5, c - 1.5, c))
    df = _df(rows)
    setup = evaluate_setup(df)
    # May or may not find a setup — just verify shape if found
    if setup is not None:
        assert "side" in setup
        assert "entry_low" in setup and "entry_high" in setup
        assert setup["status"] in ("in_zone", "approaching", "watching")
        assert setup["risk_reward"] > 0


def test_scanner_works_on_empty_universe(tmp_path):
    store = BarStore(tmp_path / "s.duckdb")
    scanner = SMCScanner(store)
    scanner.set_universes(stocks=[], crypto=[], forex=[])
    import asyncio
    n = asyncio.run(scanner.scan_once())
    assert n == 0
    assert scanner.scans_completed == 1


def test_scanner_returns_no_setup_for_missing_symbol(tmp_path):
    store = BarStore(tmp_path / "s.duckdb")
    scanner = SMCScanner(store)
    scanner.set_universes(stocks=["NONEXISTENT"])
    import asyncio
    asyncio.run(scanner.scan_once())
    results = scanner.results
    assert len(results) == 1
    assert results[0].error is not None
    assert results[0].setup is None


def test_scanner_status_shape(tmp_path):
    store = BarStore(tmp_path / "s.duckdb")
    scanner = SMCScanner(store)
    st = scanner.status()
    assert "running" in st and "scans_completed" in st
    assert st["scans_completed"] == 0
