"""Order manager — idempotent submission with retry on transient errors.

Strategies emit Signals → risk manager produces OrderRequests → this manager
sends them. Tracks submitted/filled orders.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ea.brokers.base import Broker
from ea.brokers.models import Order, OrderRequest, OrderStatus
from ea.eventbus import EventBus, get_bus
from ea.logging import logger


@dataclass
class OrderRecord:
    request: OrderRequest
    order: Order | None = None
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None
    attempts: int = 0


class OrderManager:
    def __init__(self, broker: Broker, bus: EventBus | None = None):
        self._broker = broker
        self._bus = bus or get_bus()
        self._records: dict[str, OrderRecord] = {}  # client_order_id -> record
        self._recent: deque[OrderRecord] = deque(maxlen=200)

    @property
    def recent(self) -> list[OrderRecord]:
        return list(self._recent)

    async def submit(self, request: OrderRequest, max_retries: int = 2) -> OrderRecord:
        # Idempotency: if we've already submitted this client_order_id, return existing
        existing = self._records.get(request.client_order_id)
        if existing is not None and existing.order is not None:
            return existing

        record = OrderRecord(request=request)
        self._records[request.client_order_id] = record
        self._recent.appendleft(record)

        for attempt in range(max_retries + 1):
            record.attempts = attempt + 1
            try:
                order = await self._broker.submit_order(request)
                record.order = order
                record.error = None
                logger.info("order submitted: {} {} {} qty={} -> {}",
                            request.side.value, request.symbol, request.order_type.value,
                            request.quantity, order.order_id)
                return record
            except Exception as e:
                record.error = str(e)
                msg = str(e).lower()
                if "client_order_id" in msg or "duplicate" in msg or "buying_power" in msg:
                    # don't retry permanent failures
                    logger.warning("order rejected (no retry): {}", e)
                    return record
                if attempt < max_retries:
                    logger.warning("order submit attempt {} failed: {} — retrying", attempt + 1, e)
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    logger.warning("order submit failed after {} attempts: {}", max_retries + 1, e)
        return record

    async def cancel(self, order_id: str) -> bool:
        try:
            await self._broker.cancel_order(order_id)
            return True
        except Exception as e:
            logger.warning("cancel failed for {}: {}", order_id, e)
            return False

    def status(self) -> dict:
        recent = list(self._recent)[:50]
        submitted = sum(1 for r in recent if r.order is not None)
        failed = sum(1 for r in recent if r.order is None and r.error is not None)
        return {
            "submitted": submitted,
            "failed": failed,
            "total_records": len(self._records),
        }
