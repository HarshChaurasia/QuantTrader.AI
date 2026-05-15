"""Strategy runner — subscribes to the bus, dispatches to registered strategies.

Spawned at server startup. Each strategy gets the same event stream and contributes
Signals. The runner publishes Signal objects back onto the bus for risk + order
manager to consume.
"""
from __future__ import annotations

import asyncio
from typing import Iterable

from ea.brokers.models import BarEvent
from ea.eventbus import EventBus, get_bus
from ea.logging import logger
from ea.news.models import NewsEvent
from ea.strategies.base import Context, Signal, Strategy


class StrategyRunner:
    def __init__(
        self,
        strategies: Iterable[Strategy],
        ctx: Context,
        bus: EventBus | None = None,
    ):
        self._strategies = list(strategies)
        self._ctx = ctx
        self._bus = bus or get_bus()
        self._enabled: dict[str, bool] = {s.name: True for s in self._strategies}
        self._task: asyncio.Task | None = None
        self._stop_evt = asyncio.Event()
        self._emitted_total = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def strategies(self) -> list[Strategy]:
        return list(self._strategies)

    @property
    def emitted_total(self) -> int:
        return self._emitted_total

    def pause(self, name: str) -> bool:
        if name in self._enabled:
            self._enabled[name] = False
            return True
        return False

    def resume(self, name: str) -> bool:
        if name in self._enabled:
            self._enabled[name] = True
            return True
        return False

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)

    def status(self) -> list[dict]:
        return [
            {"name": s.name, "enabled": self._enabled.get(s.name, False),
             "asset_classes": [c.value for c in s.asset_classes]}
            for s in self._strategies
        ]

    async def _dispatch(self, event) -> list[Signal]:
        out: list[Signal] = []
        for s in self._strategies:
            if not self._enabled.get(s.name, False):
                continue
            try:
                if isinstance(event, BarEvent):
                    out.extend(s.on_bar(event, self._ctx))
                elif isinstance(event, NewsEvent):
                    out.extend(s.on_news(event, self._ctx))
            except Exception as e:
                logger.warning("strategy {} error: {}", s.name, e)
        return out

    async def _run(self) -> None:
        q = self._bus.subscribe(maxsize=500)
        logger.info("StrategyRunner started: {}", [s.name for s in self._strategies])
        try:
            while not self._stop_evt.is_set():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                signals = await self._dispatch(event)
                for sig in signals:
                    self._emitted_total += 1
                    await self._bus.publish(sig)
        finally:
            self._bus.unsubscribe(q)
            logger.info("StrategyRunner stopped; emitted={}", self._emitted_total)

    def start(self) -> None:
        if self.running:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run(), name="strategy-runner")

    async def stop(self) -> None:
        self._stop_evt.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
