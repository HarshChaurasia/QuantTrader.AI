"""Broker-agnostic pydantic models.

Every broker adapter normalizes its native types into these. Strategy/risk/execution
code only sees these — never SDK-native objects. That's what lets us swap Alpaca for
OANDA (or add IBKR later) without touching anything above the broker layer.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetClass(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    FOREX = "forex"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PENDING = "pending"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class Account(BaseModel):
    """Snapshot of broker account state at a point in time."""
    model_config = ConfigDict(frozen=True)

    account_id: str
    currency: str = "USD"
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    portfolio_value: Decimal
    pattern_day_trader: bool = False
    trading_blocked: bool = False
    account_blocked: bool = False
    transfers_blocked: bool = False
    raw: dict | None = Field(default=None, exclude=True)  # original SDK object for debugging


class Position(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    asset_class: AssetClass
    quantity: Decimal           # signed: positive = long, negative = short
    avg_entry_price: Decimal
    current_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pl: Decimal | None = None
    unrealized_pl_pct: float | None = None
    cost_basis: Decimal | None = None


class OrderRequest(BaseModel):
    """What strategy code creates; risk manager validates; order manager submits."""
    model_config = ConfigDict(frozen=True)

    symbol: str
    asset_class: AssetClass
    side: OrderSide
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    client_order_id: str         # required for idempotency — never auto-generated downstream


class Order(BaseModel):
    """Broker's view of an order after submission."""
    model_config = ConfigDict(frozen=True)

    order_id: str
    client_order_id: str
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    quantity: Decimal
    filled_quantity: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    order_type: OrderType
    status: OrderStatus
    submitted_at: datetime
    filled_at: datetime | None = None
    canceled_at: datetime | None = None


# --- Streaming events ---

class BrokerEventType(str, Enum):
    BAR = "bar"
    QUOTE = "quote"
    TRADE = "trade"
    FILL = "fill"
    ORDER_UPDATE = "order_update"
    ACCOUNT_UPDATE = "account_update"
    CONNECTION = "connection"


class BarEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal[BrokerEventType.BAR] = BrokerEventType.BAR
    symbol: str
    asset_class: AssetClass
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timeframe: str   # e.g. "1Min", "1Day"


class FillEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal[BrokerEventType.FILL] = BrokerEventType.FILL
    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    fill_price: Decimal
    timestamp: datetime


# Discriminated union — add other event types here as we implement them
BrokerEvent = BarEvent | FillEvent
