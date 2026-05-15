"""Strategy framework.

A Strategy receives events (bars, news) and emits Signals. The risk manager
validates+sizes; the order manager submits. Strategies never call the broker
directly. They are pure-ish observers of the event stream.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from ea.brokers.models import AssetClass
from ea.news.models import NewsEvent


class SignalSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"   # close any existing position


@dataclass(frozen=True)
class Signal:
    strategy: str
    symbol: str
    asset_class: AssetClass
    side: SignalSide
    conviction: float                 # 0..1
    horizon_days: int                 # holding period target
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    target_size_pct: float | None = None  # if set, strategy's preferred size — risk may downsize
    stop_atr_mult: float = 1.0
    take_atr_mult: float = 2.0
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """Read-only view passed to strategies. Filled in by the orchestrator."""
    bar_store: Any
    state: Any


class Strategy(ABC):
    name: str = "base"
    asset_classes: set[AssetClass] = {AssetClass.STOCK}

    def on_bar(self, event: Any, ctx: Context) -> list[Signal]:
        return []

    def on_news(self, event: NewsEvent, ctx: Context) -> list[Signal]:
        return []

    def on_fill(self, event: Any, ctx: Context) -> None:
        pass
