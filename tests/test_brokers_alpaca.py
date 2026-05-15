"""Tests for AlpacaBroker.

Two layers:
1. Mocked tests (always run): verify the adapter correctly translates SDK objects
   into our pydantic models.
2. Live smoke test (skipped unless ALPACA_KEY_ID + ALPACA_SECRET_KEY present):
   actually hits the paper API. Read-only, no orders.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ea.brokers.alpaca.client import AlpacaBroker
from ea.brokers.models import AssetClass
from ea.config import load_config


def _fake_trading_client(account_obj, positions_list):
    m = MagicMock()
    m.get_account.return_value = account_obj
    m.get_all_positions.return_value = positions_list
    return m


@pytest.mark.asyncio
async def test_get_account_translates_sdk_object():
    cfg = load_config(profile="paper")
    fake_account = SimpleNamespace(
        id="ACCT123",
        currency="USD",
        cash="5000.00",
        equity="5500.00",
        buying_power="11000.00",
        portfolio_value="5500.00",
        pattern_day_trader=False,
        trading_blocked=False,
        account_blocked=False,
        transfers_blocked=False,
    )
    broker = AlpacaBroker(
        cfg,
        trading_client=_fake_trading_client(fake_account, []),
        stock_data_client=MagicMock(),
        crypto_data_client=MagicMock(),
    )
    account = await broker.get_account()
    assert account.account_id == "ACCT123"
    assert account.equity == Decimal("5500.00")
    assert account.buying_power == Decimal("11000.00")
    assert account.pattern_day_trader is False


@pytest.mark.asyncio
async def test_get_positions_normalizes_short_quantity():
    cfg = load_config(profile="paper")
    long_pos = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="10", side="long",
        avg_entry_price="150", current_price="155", market_value="1550",
        unrealized_pl="50", unrealized_plpc="0.0333", cost_basis="1500",
    )
    short_pos = SimpleNamespace(
        symbol="TSLA", asset_class="us_equity", qty="3", side="short",
        avg_entry_price="200", current_price="195", market_value="-585",
        unrealized_pl="15", unrealized_plpc="0.025", cost_basis="-600",
    )
    crypto_pos = SimpleNamespace(
        symbol="BTC/USD", asset_class="crypto", qty="0.5", side="long",
        avg_entry_price="50000", current_price="51000", market_value="25500",
        unrealized_pl="500", unrealized_plpc="0.02", cost_basis="25000",
    )
    broker = AlpacaBroker(
        cfg,
        trading_client=_fake_trading_client(SimpleNamespace(
            id="X", currency="USD", cash="0", equity="0",
            buying_power="0", portfolio_value="0",
        ), [long_pos, short_pos, crypto_pos]),
        stock_data_client=MagicMock(),
        crypto_data_client=MagicMock(),
    )
    positions = await broker.get_positions()
    assert len(positions) == 3
    by_sym = {p.symbol: p for p in positions}
    assert by_sym["AAPL"].quantity == Decimal("10")
    assert by_sym["TSLA"].quantity == Decimal("-3")           # short → negative
    assert by_sym["BTC/USD"].asset_class == AssetClass.CRYPTO
    assert by_sym["BTC/USD"].quantity == Decimal("0.5")


def test_init_raises_without_credentials(monkeypatch):
    """If alpaca creds aren't set anywhere, construction must fail loudly."""
    monkeypatch.delenv("ALPACA_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    cfg = load_config(profile="paper")
    # Force-clear in case .env populated them
    object.__setattr__(cfg.env, "alpaca_key_id", None)
    object.__setattr__(cfg.env, "alpaca_secret_key", None)

    with pytest.raises(RuntimeError, match="Alpaca credentials missing"):
        AlpacaBroker(cfg)


# --- Live smoke test (skipped without keys) ---

_HAS_ALPACA = bool(os.environ.get("ALPACA_KEY_ID") and os.environ.get("ALPACA_SECRET_KEY"))


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_ALPACA, reason="ALPACA_KEY_ID/SECRET not set")
async def test_live_get_account_paper():
    """Hits Alpaca paper API. Skipped if no credentials in env."""
    cfg = load_config(profile="paper")
    broker = AlpacaBroker(cfg)
    account = await broker.get_account()
    assert account.account_id
    assert account.equity >= Decimal(0)


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_ALPACA, reason="ALPACA_KEY_ID/SECRET not set")
async def test_live_get_bars_spy_recent():
    """Fetch 5 days of daily SPY bars from paper API."""
    cfg = load_config(profile="paper")
    broker = AlpacaBroker(cfg)
    end = datetime.now(timezone.utc) - timedelta(minutes=20)  # avoid the 15-min IEX delay
    start = end - timedelta(days=10)
    df = await broker.get_bars("SPY", "1Day", start, end, AssetClass.STOCK)
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)
