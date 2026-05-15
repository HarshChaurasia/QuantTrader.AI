"""Operational alert monitor.

Periodically inspects the live subsystems and raises dashboard alerts for the
three conditions that matter during an unattended paper run:

1. a risk circuit breaker tripped (daily / weekly halt),
2. a data stream that was started is no longer running (disconnect),
3. an order stuck unfilled past a staleness threshold, or that errored.

Alerts are de-duplicated by a stable key so a persistent condition produces
one alert, not one per tick.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from ea.logging import logger

# order statuses that count as "still working"
_OPEN_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "pending"}


def evaluate_alerts(
    *,
    risk: Any | None,
    streams: dict[str, Any] | None,
    order_mgr: Any | None,
    now: datetime | None = None,
    stale_order_s: float = 300.0,
) -> list[tuple[str, str, str]]:
    """Return a list of (key, level, message). Pure — no side effects."""
    now = now or datetime.now(timezone.utc)
    out: list[tuple[str, str, str]] = []

    if risk is not None:
        try:
            snap = risk.snapshot()
            if snap.get("daily_halted"):
                out.append((
                    "risk:daily", "danger",
                    f"Daily loss breaker tripped ({snap.get('daily_loss_pct')}%) — trading halted",
                ))
            if snap.get("weekly_halted"):
                out.append((
                    "risk:weekly", "danger",
                    f"Weekly loss breaker tripped ({snap.get('weekly_loss_pct')}%) — trading halted",
                ))
        except Exception as e:
            logger.debug("alerts: risk snapshot failed: {}", e)

    for name, runner in (streams or {}).items():
        try:
            if not getattr(runner, "running", True):
                out.append((
                    f"stream:{name}", "warning",
                    f"{name} stream disconnected — no live bars until it reconnects",
                ))
        except Exception as e:
            logger.debug("alerts: stream {} check failed: {}", name, e)

    if order_mgr is not None:
        try:
            for r in order_mgr.recent:
                cid = r.request.client_order_id
                if r.error:
                    out.append((
                        f"order_err:{cid}", "warning",
                        f"Order {r.request.symbol} failed: {r.error}",
                    ))
                    continue
                if r.order is None:
                    continue
                status = r.order.status.value
                age = (now - r.submitted_at).total_seconds()
                if status in _OPEN_STATUSES and age >= stale_order_s:
                    out.append((
                        f"order_stale:{cid}", "warning",
                        f"Order {r.request.symbol} unfilled for {int(age)}s (status={status})",
                    ))
        except Exception as e:
            logger.debug("alerts: order scan failed: {}", e)

    return out


class AlertMonitor:
    """Background loop that turns evaluate_alerts() output into dashboard alerts."""

    def __init__(self, state: Any, *, risk=None, streams=None, order_mgr=None,
                 interval_s: float = 60.0):
        self._state = state
        self._risk = risk
        self._streams = streams
        self._order_mgr = order_mgr
        self._interval = interval_s
        self._seen: set[str] = set()
        self._task: asyncio.Task | None = None

    def _emit(self, items: list[tuple[str, str, str]]) -> None:
        active_keys = {k for k, _, _ in items}
        for key, level, msg in items:
            if key in self._seen:
                continue
            self._seen.add(key)
            self._state.add_alert(level, msg)
        # allow a cleared condition to alert again next time it occurs
        self._seen &= active_keys

    async def _run(self) -> None:
        logger.info("AlertMonitor started (interval={}s)", self._interval)
        try:
            while True:
                items = evaluate_alerts(
                    risk=self._risk, streams=self._streams, order_mgr=self._order_mgr,
                )
                self._emit(items)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("AlertMonitor stopped")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="alert-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
