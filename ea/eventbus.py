"""Async pub/sub event bus.

One bus per process. Subscribers get their own bounded `asyncio.Queue`; publishers
fan out non-blocking. If a subscriber falls behind, we drop the oldest event in its
queue (not the new one) — keeps the freshest data flowing rather than the staler.

Publishing is thread-safe via `publish_threadsafe()`, so callbacks from
alpaca-py's WebSocket thread can hand events back to our event loop without
sharing a loop with them.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ea.logging import logger


class EventBus:
    """Lightweight async pub/sub."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dropped_total = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped_total(self) -> int:
        return self._dropped_total

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Capture the loop used for cross-thread publishes. Idempotent."""
        self._loop = loop or asyncio.get_running_loop()

    def subscribe(self, maxsize: int = 200) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def publish(self, event: Any) -> None:
        """Fan out to all subscribers. Never blocks: drops oldest on full queues."""
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped_total += 1
                try:
                    q.get_nowait()  # drop oldest
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # subscriber is wedged; skip

    def publish_threadsafe(self, event: Any) -> None:
        """Publish from a non-asyncio thread. Requires bind_loop() called first."""
        if self._loop is None:
            logger.warning("publish_threadsafe called before bind_loop; event dropped")
            return
        asyncio.run_coroutine_threadsafe(self.publish(event), self._loop)

    async def stream(self, maxsize: int = 200) -> AsyncIterator[Any]:
        """Convenience: subscribe and yield events forever."""
        q = self.subscribe(maxsize=maxsize)
        try:
            while True:
                yield await q.get()
        finally:
            self.unsubscribe(q)


# Process-wide singleton — modules that don't want to thread a reference can use this.
_BUS: EventBus | None = None


def get_bus() -> EventBus:
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS


def reset_bus() -> None:
    """Test helper — fresh bus per test."""
    global _BUS
    _BUS = None
