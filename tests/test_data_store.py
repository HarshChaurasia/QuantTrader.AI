"""Tests for ea.data.store — DuckDB upsert idempotency, reads, indexing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ea.brokers.models import AssetClass
from ea.data.store import BarStore


def _make_bars(n: int = 5, start_price: float = 100.0) -> pd.DataFrame:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = [base + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "open":   [start_price + i for i in range(n)],
        "high":   [start_price + i + 1 for i in range(n)],
        "low":    [start_price + i - 1 for i in range(n)],
        "close":  [start_price + i + 0.5 for i in range(n)],
        "volume": [1_000_000 + i * 10_000 for i in range(n)],
    }, index=pd.DatetimeIndex(idx, name="ts"))


def test_store_initializes_and_is_empty(tmp_path):
    s = BarStore(tmp_path / "t.duckdb")
    assert s.row_count() == 0
    assert s.list_symbols() == []


def test_upsert_and_read_roundtrip(tmp_path):
    s = BarStore(tmp_path / "t.duckdb")
    df = _make_bars(5)
    n = s.upsert_bars(df, "AAPL", "1Day", AssetClass.STOCK)
    assert n == 5
    assert s.row_count() == 5

    back = s.get_bars("AAPL", "1Day")
    assert len(back) == 5
    assert list(back.columns) == ["open", "high", "low", "close", "volume"]
    assert float(back["close"].iloc[0]) == pytest.approx(100.5)


def test_upsert_is_idempotent(tmp_path):
    """Re-inserting the same range must overwrite, not duplicate."""
    s = BarStore(tmp_path / "t.duckdb")
    df = _make_bars(5)
    s.upsert_bars(df, "AAPL", "1Day", AssetClass.STOCK)
    s.upsert_bars(df, "AAPL", "1Day", AssetClass.STOCK)  # second time
    assert s.row_count() == 5


def test_upsert_overlapping_overwrites(tmp_path):
    """Overlapping ranges: new values for shared timestamps must win."""
    s = BarStore(tmp_path / "t.duckdb")
    df1 = _make_bars(5, start_price=100)
    s.upsert_bars(df1, "AAPL", "1Day", AssetClass.STOCK)

    df2 = _make_bars(5, start_price=200)  # same dates, different prices
    s.upsert_bars(df2, "AAPL", "1Day", AssetClass.STOCK)

    back = s.get_bars("AAPL", "1Day")
    assert len(back) == 5
    assert float(back["close"].iloc[0]) == pytest.approx(200.5)  # df2's value, not df1's


def test_get_latest_ts(tmp_path):
    s = BarStore(tmp_path / "t.duckdb")
    assert s.get_latest_ts("AAPL", "1Day") is None
    s.upsert_bars(_make_bars(5), "AAPL", "1Day", AssetClass.STOCK)
    latest = s.get_latest_ts("AAPL", "1Day")
    assert latest is not None
    assert latest.date() == datetime(2024, 1, 5).date()


def test_list_symbols_filters_by_class(tmp_path):
    s = BarStore(tmp_path / "t.duckdb")
    s.upsert_bars(_make_bars(3), "AAPL", "1Day", AssetClass.STOCK)
    s.upsert_bars(_make_bars(3), "MSFT", "1Day", AssetClass.STOCK)
    s.upsert_bars(_make_bars(3), "BTC/USD", "1Day", AssetClass.CRYPTO)

    stocks = s.list_symbols(AssetClass.STOCK)
    crypto = s.list_symbols(AssetClass.CRYPTO)
    assert set(stocks) == {"AAPL", "MSFT"}
    assert crypto == ["BTC/USD"]


def test_get_bars_filters_by_date_range(tmp_path):
    s = BarStore(tmp_path / "t.duckdb")
    s.upsert_bars(_make_bars(10), "AAPL", "1Day", AssetClass.STOCK)
    start = datetime(2024, 1, 3, tzinfo=timezone.utc)
    end = datetime(2024, 1, 7, tzinfo=timezone.utc)
    back = s.get_bars("AAPL", "1Day", start=start, end=end)
    assert len(back) == 5  # Jan 3,4,5,6,7


def test_missing_required_columns_raises(tmp_path):
    s = BarStore(tmp_path / "t.duckdb")
    bad = pd.DataFrame({"open": [1], "close": [1]}, index=[datetime(2024, 1, 1)])
    with pytest.raises(ValueError, match="Missing required columns"):
        s.upsert_bars(bad, "AAPL", "1Day", AssetClass.STOCK)
