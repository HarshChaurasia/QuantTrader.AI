"""Tests for ea.data.universe — liquidity filter logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from ea.brokers.models import AssetClass
from ea.config import load_config
from ea.data.store import BarStore
from ea.data.universe import scan_liquid_universe


def _bars_for(price: float, volume: float, days: int = 20) -> pd.DataFrame:
    base = datetime.now(timezone.utc) - timedelta(days=days)
    idx = [base + timedelta(days=i) for i in range(days)]
    return pd.DataFrame({
        "open":   [price] * days,
        "high":   [price * 1.01] * days,
        "low":    [price * 0.99] * days,
        "close":  [price] * days,
        "volume": [volume] * days,
    }, index=pd.DatetimeIndex(idx, name="ts"))


def test_universe_filters_by_price_floor(tmp_path):
    """Symbol under $5 must be excluded regardless of volume."""
    cfg = load_config(profile="paper")
    s = BarStore(tmp_path / "u.duckdb")
    s.upsert_bars(_bars_for(price=3.0, volume=10_000_000), "PENNY", "1Day", AssetClass.STOCK)
    s.upsert_bars(_bars_for(price=100.0, volume=500_000), "BIG", "1Day", AssetClass.STOCK)

    entries = scan_liquid_universe(cfg, s)
    symbols = [e.symbol for e in entries]
    assert "PENNY" not in symbols
    assert "BIG" in symbols  # $100 * 500k = $50M ADV > $10M floor


def test_universe_filters_by_dollar_volume(tmp_path):
    """Symbol with ADV under $10M must be excluded."""
    cfg = load_config(profile="paper")
    s = BarStore(tmp_path / "u.duckdb")
    # Above $5 but ADV = $10 * 100k = $1M — under $10M floor
    s.upsert_bars(_bars_for(price=10.0, volume=100_000), "ILLIQUID", "1Day", AssetClass.STOCK)
    # ADV = $50 * 1M = $50M — passes
    s.upsert_bars(_bars_for(price=50.0, volume=1_000_000), "LIQUID", "1Day", AssetClass.STOCK)

    entries = scan_liquid_universe(cfg, s)
    symbols = [e.symbol for e in entries]
    assert "ILLIQUID" not in symbols
    assert "LIQUID" in symbols


def test_universe_sorted_descending_by_adv(tmp_path):
    cfg = load_config(profile="paper")
    s = BarStore(tmp_path / "u.duckdb")
    s.upsert_bars(_bars_for(price=100, volume=1_000_000), "MID", "1Day", AssetClass.STOCK)   # $100M ADV
    s.upsert_bars(_bars_for(price=100, volume=10_000_000), "TOP", "1Day", AssetClass.STOCK)  # $1B ADV
    s.upsert_bars(_bars_for(price=20, volume=2_000_000), "LOW", "1Day", AssetClass.STOCK)    # $40M ADV

    entries = scan_liquid_universe(cfg, s)
    assert [e.symbol for e in entries] == ["TOP", "MID", "LOW"]


def test_universe_skips_symbols_with_too_few_bars(tmp_path):
    cfg = load_config(profile="paper")
    s = BarStore(tmp_path / "u.duckdb")
    s.upsert_bars(_bars_for(price=100, volume=1_000_000, days=2), "TOOFEW", "1Day", AssetClass.STOCK)
    entries = scan_liquid_universe(cfg, s)
    assert "TOOFEW" not in [e.symbol for e in entries]
