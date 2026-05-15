"""Alpaca broker adapter.

Wraps `alpaca-py` (which is sync) and exposes the async `Broker` interface by
running blocking calls in a worker thread (`asyncio.to_thread`). Sufficient for
read-only / event-driven workloads at swing horizons; we are not optimizing for
sub-millisecond latency.

Phase 0: read methods only (`get_account`, `get_positions`, `get_bars`).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from alpaca.data.historical import (
    CryptoHistoricalDataClient,
    StockHistoricalDataClient,
)
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from ea.brokers.base import Broker
from ea.brokers.models import (
    Account, AssetClass, Order, OrderRequest, OrderSide, OrderStatus, OrderType, Position,
    TimeInForce,
)
from ea.config import Config
from ea.logging import logger

# Map our string timeframes to alpaca-py TimeFrame objects.
_TIMEFRAME_MAP: dict[str, TimeFrame] = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "1Day": TimeFrame(1, TimeFrameUnit.Day),
}


def _to_decimal(value: Any) -> Decimal:
    """Coerce to Decimal, treating None as 0."""
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


def _classify_asset(asset_class_raw: Any) -> AssetClass:
    """Alpaca returns asset class as enum or string; normalize to ours."""
    s = str(asset_class_raw).lower()
    if "crypto" in s:
        return AssetClass.CRYPTO
    return AssetClass.STOCK


class AlpacaBroker(Broker):
    """Alpaca paper/live broker adapter — supports US stocks and crypto."""

    name = "alpaca"
    supported_asset_classes = {AssetClass.STOCK, AssetClass.CRYPTO}

    def __init__(self, config: Config, *, trading_client=None, stock_data_client=None, crypto_data_client=None):
        """Construct from config; optional client overrides for tests (dependency injection)."""
        self._config = config
        env = config.env

        if trading_client is None:
            if env.alpaca_key_id is None or env.alpaca_secret_key is None:
                raise RuntimeError(
                    "Alpaca credentials missing — set ALPACA_KEY_ID and ALPACA_SECRET_KEY in .env"
                )
            self._trading = TradingClient(
                api_key=env.alpaca_key_id.get_secret_value(),
                secret_key=env.alpaca_secret_key.get_secret_value(),
                paper=not config.is_live,
            )
        else:
            self._trading = trading_client

        if stock_data_client is None and env.alpaca_key_id and env.alpaca_secret_key:
            self._stock_data = StockHistoricalDataClient(
                api_key=env.alpaca_key_id.get_secret_value(),
                secret_key=env.alpaca_secret_key.get_secret_value(),
            )
        else:
            self._stock_data = stock_data_client

        if crypto_data_client is None and env.alpaca_key_id and env.alpaca_secret_key:
            self._crypto_data = CryptoHistoricalDataClient(
                api_key=env.alpaca_key_id.get_secret_value(),
                secret_key=env.alpaca_secret_key.get_secret_value(),
            )
        else:
            self._crypto_data = crypto_data_client

        logger.info(
            "AlpacaBroker initialized (paper={}, base_url={})",
            not config.is_live,
            env.alpaca_base_url,
        )

    # --- Read methods ---

    async def get_account(self) -> Account:
        raw = await asyncio.to_thread(self._trading.get_account)
        return Account(
            account_id=str(raw.id),
            currency=str(raw.currency),
            cash=_to_decimal(raw.cash),
            equity=_to_decimal(raw.equity),
            buying_power=_to_decimal(raw.buying_power),
            portfolio_value=_to_decimal(raw.portfolio_value),
            pattern_day_trader=bool(getattr(raw, "pattern_day_trader", False)),
            trading_blocked=bool(getattr(raw, "trading_blocked", False)),
            account_blocked=bool(getattr(raw, "account_blocked", False)),
            transfers_blocked=bool(getattr(raw, "transfers_blocked", False)),
        )

    async def get_positions(self) -> list[Position]:
        raw_positions = await asyncio.to_thread(self._trading.get_all_positions)
        positions: list[Position] = []
        for p in raw_positions:
            qty = _to_decimal(p.qty)
            # Alpaca stores short qty as positive with side=short; normalize to signed.
            if str(getattr(p, "side", "long")).lower() == "short":
                qty = -qty
            positions.append(
                Position(
                    symbol=str(p.symbol),
                    asset_class=_classify_asset(getattr(p, "asset_class", "us_equity")),
                    quantity=qty,
                    avg_entry_price=_to_decimal(p.avg_entry_price),
                    current_price=_to_decimal(getattr(p, "current_price", None)) or None,
                    market_value=_to_decimal(getattr(p, "market_value", None)) or None,
                    unrealized_pl=_to_decimal(getattr(p, "unrealized_pl", None)) or None,
                    unrealized_pl_pct=(
                        float(getattr(p, "unrealized_plpc", 0.0))
                        if getattr(p, "unrealized_plpc", None) is not None
                        else None
                    ),
                    cost_basis=_to_decimal(getattr(p, "cost_basis", None)) or None,
                )
            )
        return positions

    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        asset_class: AssetClass = AssetClass.STOCK,
    ) -> pd.DataFrame:
        tf = _TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}. Supported: {list(_TIMEFRAME_MAP)}")

        if asset_class == AssetClass.STOCK:
            if self._stock_data is None:
                raise RuntimeError("Stock data client not initialized (missing credentials).")
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                feed=self._config.profile.broker.alpaca.data_feed,
            )
            bars = await asyncio.to_thread(self._stock_data.get_stock_bars, req)
        elif asset_class == AssetClass.CRYPTO:
            if self._crypto_data is None:
                raise RuntimeError("Crypto data client not initialized (missing credentials).")
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
            )
            bars = await asyncio.to_thread(self._crypto_data.get_crypto_bars, req)
        else:
            raise ValueError(f"AlpacaBroker does not support asset class {asset_class}")

        df = bars.df  # alpaca-py returns BarSet with .df property → MultiIndex (symbol, timestamp)
        if df is None or df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel(0)
        return df[["open", "high", "low", "close", "volume"]]

    # --- Write methods (Phase A.8) ---

    async def submit_order(self, request: OrderRequest) -> Order:
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
        from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce as AlpacaTIF

        side_map = {OrderSide.BUY: AlpacaSide.BUY, OrderSide.SELL: AlpacaSide.SELL}
        tif_map = {
            TimeInForce.DAY: AlpacaTIF.DAY, TimeInForce.GTC: AlpacaTIF.GTC,
            TimeInForce.IOC: AlpacaTIF.IOC, TimeInForce.FOK: AlpacaTIF.FOK,
        }

        if request.order_type == OrderType.LIMIT:
            req = LimitOrderRequest(
                symbol=request.symbol,
                qty=float(request.quantity),
                side=side_map[request.side],
                time_in_force=tif_map[request.time_in_force],
                limit_price=float(request.limit_price) if request.limit_price else None,
                client_order_id=request.client_order_id,
            )
        else:
            req = MarketOrderRequest(
                symbol=request.symbol,
                qty=float(request.quantity),
                side=side_map[request.side],
                time_in_force=tif_map[request.time_in_force],
                client_order_id=request.client_order_id,
            )

        raw = await asyncio.to_thread(self._trading.submit_order, req)
        return self._normalize_order(raw, request)

    async def cancel_order(self, order_id: str) -> None:
        import uuid as _uuid
        await asyncio.to_thread(self._trading.cancel_order_by_id, _uuid.UUID(order_id))

    async def list_open_orders(self) -> list[Order]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        raw = await asyncio.to_thread(self._trading.get_orders, req)
        return [self._normalize_order(o) for o in raw]

    async def close_all_positions(self) -> int:
        """Cancel all open orders and flatten all positions. Returns count closed."""
        await asyncio.to_thread(self._trading.cancel_orders)
        positions = await self.get_positions()
        for p in positions:
            try:
                await asyncio.to_thread(self._trading.close_position, p.symbol)
            except Exception as e:
                logger.warning("close_position failed for {}: {}", p.symbol, e)
        return len(positions)

    def _normalize_order(self, raw: Any, request: OrderRequest | None = None) -> Order:
        status_map = {
            "new": OrderStatus.NEW, "accepted": OrderStatus.NEW, "pending_new": OrderStatus.PENDING,
            "partially_filled": OrderStatus.PARTIALLY_FILLED, "filled": OrderStatus.FILLED,
            "done_for_day": OrderStatus.EXPIRED, "canceled": OrderStatus.CANCELED,
            "expired": OrderStatus.EXPIRED, "rejected": OrderStatus.REJECTED,
        }
        type_map = {"market": OrderType.MARKET, "limit": OrderType.LIMIT,
                    "stop": OrderType.STOP, "stop_limit": OrderType.STOP_LIMIT}
        side_str = str(getattr(raw, "side", "buy")).lower().split(".")[-1]
        status_str = str(getattr(raw, "status", "new")).lower().split(".")[-1]
        type_str = str(getattr(raw, "type", "market") or getattr(raw, "order_type", "market")).lower().split(".")[-1]

        submitted = getattr(raw, "submitted_at", None) or getattr(raw, "created_at", None) or datetime.now(timezone.utc)
        ac = AssetClass.CRYPTO if (request and request.asset_class == AssetClass.CRYPTO) else AssetClass.STOCK

        return Order(
            order_id=str(raw.id),
            client_order_id=str(getattr(raw, "client_order_id", "") or (request.client_order_id if request else "")),
            symbol=str(raw.symbol),
            asset_class=ac,
            side=OrderSide.BUY if side_str == "buy" else OrderSide.SELL,
            quantity=Decimal(str(raw.qty)),
            filled_quantity=Decimal(str(getattr(raw, "filled_qty", 0) or 0)),
            avg_fill_price=(Decimal(str(getattr(raw, "filled_avg_price", 0))) if getattr(raw, "filled_avg_price", None) else None),
            order_type=type_map.get(type_str, OrderType.MARKET),
            status=status_map.get(status_str, OrderStatus.PENDING),
            submitted_at=submitted if isinstance(submitted, datetime) else datetime.now(timezone.utc),
            filled_at=getattr(raw, "filled_at", None) if isinstance(getattr(raw, "filled_at", None), datetime) else None,
            canceled_at=getattr(raw, "canceled_at", None) if isinstance(getattr(raw, "canceled_at", None), datetime) else None,
        )
