"""Signal consumer — bus subscriber that turns Signals into submitted Orders.

Pipeline: bus.Signal → risk.evaluate → if ok, order_manager.submit → bus.Order

This is the glue between strategy and broker. Keeps a recent-decisions log for
the dashboard signals panel.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ea.brokers.base import Broker
from ea.data.store import BarStore
from ea.eventbus import EventBus, get_bus
from ea.execution.order_manager import OrderManager, OrderRecord
from ea.logging import logger
from ea.risk.manager import RiskDecision, RiskManager
from ea.strategies.base import Signal


@dataclass
class SignalOutcome:
    signal: Signal
    decision: RiskDecision
    order_record: OrderRecord | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SignalConsumer:
    def __init__(
        self,
        broker: Broker,
        risk: RiskManager,
        order_manager: OrderManager,
        bar_store: BarStore,
        bus: EventBus | None = None,
        autosubmit: bool = True,
    ):
        self._broker = broker
        self._risk = risk
        self._om = order_manager
        self._store = bar_store
        self._bus = bus or get_bus()
        self._autosubmit = autosubmit
        self._task: asyncio.Task | None = None
        self._stop_evt = asyncio.Event()
        self._outcomes: deque[SignalOutcome] = deque(maxlen=100)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def autosubmit(self) -> bool:
        return self._autosubmit

    def set_autosubmit(self, on: bool) -> None:
        self._autosubmit = on

    @property
    def recent(self) -> list[SignalOutcome]:
        return list(self._outcomes)

    async def _process(self, sig: Signal) -> SignalOutcome:
        try:
            account = await self._broker.get_account()
            positions = await self._broker.get_positions()
        except Exception as e:
            logger.warning("signal_consumer: broker fetch failed: {}", e)
            decision = RiskDecision(ok=False, order=None, reason=f"broker fetch failed: {e}", signal=sig)
            outcome = SignalOutcome(signal=sig, decision=decision)
            self._outcomes.appendleft(outcome)
            return outcome

        decision = self._risk.evaluate(sig, account=account, positions=positions, bar_store=self._store)
        outcome = SignalOutcome(signal=sig, decision=decision)
        self._outcomes.appendleft(outcome)

        if decision.ok and decision.order is not None and self._autosubmit:
            record = await self._om.submit(decision.order)
            outcome.order_record = record
        return outcome

    async def _run(self) -> None:
        q = self._bus.subscribe(maxsize=500)
        logger.info("SignalConsumer started (autosubmit={})", self._autosubmit)
        try:
            while not self._stop_evt.is_set():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(event, Signal):
                    await self._process(event)
        finally:
            self._bus.unsubscribe(q)
            logger.info("SignalConsumer stopped")

    def start(self) -> None:
        if self.running:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run(), name="signal-consumer")

    async def stop(self) -> None:
        self._stop_evt.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
