"""Tests for ea.eventbus — publish/subscribe, backpressure drop policy."""
from __future__ import annotations

import asyncio

import pytest

from ea.eventbus import EventBus, get_bus, reset_bus


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_bus()
    yield
    reset_bus()


@pytest.mark.asyncio
async def test_basic_publish_subscribe():
    bus = EventBus()
    q = bus.subscribe()
    await bus.publish("hello")
    assert q.get_nowait() == "hello"


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive():
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    await bus.publish({"v": 1})
    assert q1.get_nowait() == {"v": 1}
    assert q2.get_nowait() == {"v": 1}


@pytest.mark.asyncio
async def test_backpressure_drops_oldest():
    bus = EventBus()
    q = bus.subscribe(maxsize=3)
    for i in range(5):
        await bus.publish(i)
    # Oldest dropped; keep newest 3
    drained = [q.get_nowait() for _ in range(3)]
    assert drained == [2, 3, 4]
    assert bus.dropped_total == 2


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    assert bus.subscriber_count == 0
    await bus.publish("nope")
    assert q.empty()


@pytest.mark.asyncio
async def test_publish_threadsafe_routes_to_bound_loop():
    bus = EventBus()
    bus.bind_loop()
    q = bus.subscribe()

    def from_thread():
        bus.publish_threadsafe("from-thread")

    import threading
    t = threading.Thread(target=from_thread)
    t.start()
    t.join()

    item = await asyncio.wait_for(q.get(), timeout=2.0)
    assert item == "from-thread"


def test_get_bus_singleton():
    a = get_bus()
    b = get_bus()
    assert a is b
    reset_bus()
    c = get_bus()
    assert c is not a
