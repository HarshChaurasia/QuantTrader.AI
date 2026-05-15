"""S2 — Catalyst-driven news momentum.

Edge hypothesis: material catalysts (FDA, M&A, guidance, contracts) cause 3-10d
drift as the buyside repositions. Enter within ~1 trading day, exit on time stop
or hard stop.

Requires LLM analysis (Phase A.5) to separate material from noise. Without
analysis, this strategy emits nothing — pure-technical news fires are too noisy.
"""
from __future__ import annotations

from ea.brokers.models import AssetClass
from ea.logging import logger
from ea.news.models import NewsEvent
from ea.strategies.base import Context, Signal, SignalSide, Strategy


class NewsMomentumStrategy(Strategy):
    name = "news_momentum"
    asset_classes = {AssetClass.STOCK, AssetClass.CRYPTO}

    def __init__(
        self,
        materiality_floor: float = 0.65,
        confidence_floor: float = 0.55,
        relevance_floor: float = 0.5,
        horizon_days: int = 5,
        stop_atr: float = 1.0,
        take_atr: float = 2.0,
    ):
        self.materiality_floor = materiality_floor
        self.confidence_floor = confidence_floor
        self.relevance_floor = relevance_floor
        self.horizon_days = horizon_days
        self.stop_atr = stop_atr
        self.take_atr = take_atr

    def on_news(self, event: NewsEvent, ctx: Context) -> list[Signal]:
        if not event.tickers:
            return []
        if event.analysis is None:
            return []  # need LLM-scored news

        a = event.analysis
        if a.relevance < self.relevance_floor:
            return []
        if a.materiality < self.materiality_floor:
            return []
        if a.confidence < self.confidence_floor:
            return []
        if a.direction_hint == 0:
            return []

        side = SignalSide.LONG if a.direction_hint > 0 else SignalSide.SHORT
        out: list[Signal] = []
        for sym in event.tickers:
            ac = AssetClass.CRYPTO if "/" in sym else AssetClass.STOCK
            out.append(Signal(
                strategy=self.name,
                symbol=sym,
                asset_class=ac,
                side=side,
                conviction=min(1.0, a.materiality * a.confidence),
                horizon_days=self.horizon_days,
                stop_atr_mult=self.stop_atr,
                take_atr_mult=self.take_atr,
                rationale=f"{a.catalyst_type.value} · {a.sentiment.value} · m={a.materiality:.2f}",
                metadata={"news_id": event.item.id, "source": event.item.source.value},
            ))
        if out:
            logger.info("news_momentum emitted {} signal(s) for {}", len(out), event.tickers)
        return out
