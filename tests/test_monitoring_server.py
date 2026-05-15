"""Tests for the dashboard FastAPI server.

Uses TestClient for in-process HTTP testing. Live broker is mocked at the state
aggregator level so tests don't hit Alpaca.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from ea.brokers.models import Account, AssetClass
from ea.config import load_config
from ea.data.store import BarStore
from ea.monitoring import state as state_mod
from ea.monitoring.server import create_app


@pytest.fixture()
def fake_broker():
    b = MagicMock()
    b.name = "alpaca"
    b.get_account = AsyncMock(return_value=Account(
        account_id="ACCT-TEST", currency="USD",
        cash=Decimal("5000"), equity=Decimal("10000"),
        buying_power=Decimal("20000"), portfolio_value=Decimal("10000"),
    ))
    b.get_positions = AsyncMock(return_value=[])
    return b


@pytest.fixture()
def app_client(tmp_path, monkeypatch, fake_broker):
    # Use a tmp store so we don't depend on user's local DuckDB
    store = BarStore(tmp_path / "test.duckdb")
    cfg = load_config(profile="paper")

    # Force a clean singleton with our fakes
    state_mod._state = state_mod.DashboardState(config=cfg, broker=fake_broker, store=store)

    # Avoid re-initializing inside create_app (it would overwrite our fakes)
    monkeypatch.setattr(state_mod, "init_state", lambda c: state_mod._state)

    app = create_app()
    return TestClient(app)


def test_index_renders(app_client):
    r = app_client.get("/")
    assert r.status_code == 200
    assert "QuantTrader" in r.text
    assert "Watchlist" in r.text
    assert "Inter" in r.text  # font loaded


def test_healthz(app_client):
    r = app_client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_account_endpoint(app_client):
    r = app_client.get("/api/account")
    assert r.status_code == 200
    data = r.json()
    assert data["account_id"] == "ACCT-TEST"
    assert data["equity"] == 10000.0


def test_positions_endpoint(app_client):
    r = app_client.get("/api/positions")
    assert r.status_code == 200
    assert r.json() == []


def test_system_endpoint(app_client):
    r = app_client.get("/api/system")
    assert r.status_code == 200
    data = r.json()
    assert data["profile"] == "paper"
    assert data["is_live"] is False
    assert data["broker"] == "alpaca"
    assert data["kill_switch_armed"] is False


def test_watchlist_default(app_client):
    r = app_client.get("/api/watchlist")
    assert r.status_code == 200
    syms = [e["symbol"] for e in r.json()]
    # default watchlist seeded
    assert "SPY" in syms and "AAPL" in syms


def test_watchlist_add_remove(app_client):
    r = app_client.post("/api/watchlist", json={"symbol": "TSLA"})
    assert r.status_code == 200
    assert "TSLA" in r.json()["watchlist"]

    r = app_client.delete("/api/watchlist/TSLA")
    assert r.status_code == 200
    assert "TSLA" not in r.json()["watchlist"]


def test_chart_returns_404_when_no_data(app_client):
    r = app_client.get("/api/chart/UNKNOWN")
    assert r.status_code == 404


def test_chart_returns_bars_when_data_present(app_client, tmp_path):
    import pandas as pd
    s = state_mod.get_state().store
    df = pd.DataFrame({
        "open": [1, 2], "high": [3, 4], "low": [0, 1],
        "close": [2, 3], "volume": [100, 200],
    }, index=pd.DatetimeIndex([
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    ], name="ts"))
    s.upsert_bars(df, "TEST", "1Day", AssetClass.STOCK)

    r = app_client.get("/api/chart/TEST")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "TEST"
    assert len(data["bars"]) == 2
    assert {"time", "open", "high", "low", "close", "volume"}.issubset(data["bars"][0].keys())


def test_kill_switch_arms(app_client):
    r = app_client.post("/api/kill")
    assert r.status_code == 200
    assert r.json()["armed"] is True

    r = app_client.get("/api/system")
    assert r.json()["kill_switch_armed"] is True

    r = app_client.post("/api/kill/disarm")
    assert r.json()["armed"] is False


def test_order_endpoint_blocked_until_phase_a8(app_client):
    r = app_client.post("/api/order", json={
        "symbol": "SPY", "side": "buy", "quantity": 1, "order_type": "market"
    })
    assert r.status_code == 503  # explicit refusal until A.8


def test_scalp_endpoint(app_client):
    """Scalp tab payload is well-formed even before lifespan/subsystems start."""
    r = app_client.get("/api/scalp")
    assert r.status_code == 200
    data = r.json()
    assert data["timeframe"] == "1Min"
    assert data["enabled"] is False  # runner not started in TestClient
    assert data["signals"] == []
    assert data["positions"] == []
    assert set(data["universe"]) == {"crypto", "forex"}
    assert "BTC/USD" in data["universe"]["crypto"]


def test_scalp_nav_tab_rendered(app_client):
    r = app_client.get("/")
    assert 'data-view="scalp"' in r.text
    assert 'data-view-content="scalp"' in r.text
