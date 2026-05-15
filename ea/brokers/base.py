"""Broker abstract base class.

All concrete brokers (Alpaca, OANDA, future IBKR) implement this. Strategy/risk/
execution code depends ONLY on this interface — never on SDK-native types.

Phase 0 implements the read methods. Write methods (submit/cancel) and streaming
remain `NotImplementedError` until T A.7 / T A.2.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

import pandas as pd

from ea.brokers.models import (
    Account,
    AssetClass,
    BrokerEvent,
    Order,
    OrderRequest,
    Position,
)


class Broker(ABC):
    """Abstract broker interface."""

    name: str
    supported_asset_classes: set[AssetClass]

    # --- Read methods (Phase 0) ---

    @abstractmethod
    async def get_account(self) -> Account:
        """Fetch current account snapshot."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Fetch all currently open positions."""

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        asset_class: AssetClass = AssetClass.STOCK,
    ) -> pd.DataFrame:
        """Fetch historical bars. Returns DataFrame indexed by timestamp with
        OHLCV columns (`open`, `high`, `low`, `close`, `volume`)."""

    # --- Write methods (T A.7) ---

    async def submit_order(self, request: OrderRequest) -> Order:
        raise NotImplementedError("submit_order not implemented at Phase 0 (T A.7)")

    async def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError("cancel_order not implemented at Phase 0 (T A.7)")

    async def get_order(self, order_id: str) -> Order:
        raise NotImplementedError("get_order not implemented at Phase 0 (T A.7)")

    async def list_open_orders(self) -> list[Order]:
        raise NotImplementedError("list_open_orders not implemented at Phase 0 (T A.7)")

    # --- Streaming (T A.2) ---

    def stream_events(self) -> AsyncIterator[BrokerEvent]:
        raise NotImplementedError("stream_events not implemented at Phase 0 (T A.2)")
