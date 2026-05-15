"""Tests for broker pydantic models — basic validation + frozen contract."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ea.brokers.models import (
    Account,
    AssetClass,
    BarEvent,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


def test_account_constructs_and_is_frozen():
    a = Account(
        account_id="X1",
        cash=Decimal("100"),
        equity=Decimal("110"),
        buying_power=Decimal("220"),
        portfolio_value=Decimal("110"),
    )
    assert a.currency == "USD"
    with pytest.raises(ValidationError):
        a.cash = Decimal("0")  # frozen


def test_position_signed_quantity():
    p = Position(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        quantity=Decimal("-5"),
        avg_entry_price=Decimal("150"),
    )
    assert p.quantity < 0


def test_order_request_requires_client_order_id():
    with pytest.raises(ValidationError):
        OrderRequest(  # type: ignore[call-arg]
            symbol="AAPL",
            asset_class=AssetClass.STOCK,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        )


def test_order_request_construction():
    req = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        client_order_id="ea-test-001",
    )
    assert req.client_order_id == "ea-test-001"
    assert req.order_type == OrderType.LIMIT


def test_order_default_filled_quantity_zero():
    o = Order(
        order_id="abc",
        client_order_id="ea-1",
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.MARKET,
        status=OrderStatus.NEW,
        submitted_at=datetime.now(timezone.utc),
    )
    assert o.filled_quantity == Decimal(0)


def test_bar_event_construction():
    b = BarEvent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        timestamp=datetime.now(timezone.utc),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("123456"),
        timeframe="1Min",
    )
    assert b.symbol == "AAPL"
