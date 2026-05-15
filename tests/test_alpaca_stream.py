"""Tests for ea.brokers.alpaca.stream — bar normalization + mocked runner.

Live network stream tested separately via the standalone smoke script — not in pytest.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ea.brokers.alpaca.stream import AlpacaStreamRunner, _normalize_bar
from ea.brokers.models import AssetClass, BarEvent
from ea.config import load_config
from ea.eventbus import EventBus, reset_bus


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_bus()
    yield
    reset_bus()


def test_normalize_bar_stocks():
    raw = SimpleNamespace(
        symbol="AAPL",
        timestamp=datetime(2024, 1, 1, 14, 30, tzinfo=timezone.utc),
        open=180.5, high=181.0, low=180.0, close=180.75, volume=1234567,
    )
    ev = _normalize_bar(raw, AssetClass.STOCK)
    assert isinstance(ev, BarEvent)
    assert ev.symbol == "AAPL"
    assert ev.asset_class == AssetClass.STOCK
    assert float(ev.close) == pytest.approx(180.75)
    assert ev.timeframe == "1Min"


def test_normalize_bar_iso_timestamp():
    raw = SimpleNamespace(
        symbol="BTC/USD", timestamp="2024-06-01T00:00:00Z",
        open=60000, high=60500, low=59800, close=60100, volume=12.5,
    )
    ev = _normalize_bar(raw, AssetClass.CRYPTO)
    assert ev.timestamp.tzinfo is not None
    assert float(ev.open) == 60000.0


def test_runner_init_rejects_forex():
    cfg = load_config(profile="paper")
    with pytest.raises(ValueError, match="stock/crypto only"):
        AlpacaStreamRunner(cfg, ["EUR/USD"], asset_class=AssetClass.FOREX)


def test_runner_init_raises_without_credentials(monkeypatch):
    cfg = load_config(profile="paper")
    object.__setattr__(cfg.env, "alpaca_key_id", None)
    object.__setattr__(cfg.env, "alpaca_secret_key", None)
    with pytest.raises(RuntimeError, match="Alpaca credentials missing"):
        AlpacaStreamRunner(cfg, ["AAPL"], asset_class=AssetClass.STOCK)


def test_runner_symbols_uppercased_and_stripped():
    cfg = load_config(profile="paper")
    runner = AlpacaStreamRunner(cfg, [" btc/usd ", "eth/usd", ""], asset_class=AssetClass.CRYPTO)
    assert runner.symbols == ["BTC/USD", "ETH/USD"]
    assert runner.asset_class == AssetClass.CRYPTO


def test_runner_status_shape():
    cfg = load_config(profile="paper")
    runner = AlpacaStreamRunner(cfg, ["BTC/USD"], asset_class=AssetClass.CRYPTO)
    st = runner.status()
    assert st["asset_class"] == "crypto"
    assert st["symbols"] == ["BTC/USD"]
    assert st["running"] is False
    assert st["started_at"] is None


@pytest.mark.asyncio
async def test_handler_publishes_to_bus_threadsafe():
    """Without booting the SDK, drive the handler directly and verify the bar lands on the bus."""
    cfg = load_config(profile="paper")
    bus = EventBus()
    bus.bind_loop()
    runner = AlpacaStreamRunner(cfg, ["BTC/USD"], asset_class=AssetClass.CRYPTO, bus=bus)
    q = bus.subscribe()

    raw = SimpleNamespace(
        symbol="BTC/USD",
        timestamp=datetime.now(timezone.utc),
        open=60000, high=60100, low=59950, close=60050, volume=10,
    )
    await runner._handle_bar(raw)
    # publish_threadsafe schedules into our loop; yield once for it to run
    await asyncio.sleep(0)
    event = await asyncio.wait_for(q.get(), timeout=2.0)
    assert isinstance(event, BarEvent)
    assert event.symbol == "BTC/USD"
    assert float(event.close) == 60050.0
