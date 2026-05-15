"""Tests for strategies, risk manager, signal consumer."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from ea.brokers.models import Account, AssetClass, Position
from ea.config import load_config
from ea.data.store import BarStore
from ea.eventbus import EventBus
from ea.execution.order_manager import OrderManager
from ea.execution.signal_consumer import SignalConsumer
from ea.news.models import (
    CatalystType, NewsAnalysis, NewsEvent, NewsItem, NewsSource, Sentiment,
)
from ea.risk.manager import RiskManager
from ea.strategies.base import Context, Signal, SignalSide
from ea.strategies.news_momentum import NewsMomentumStrategy


def _bars(tmp_path, price=100.0):
    store = BarStore(tmp_path / "t.duckdb")
    idx = pd.DatetimeIndex(
        [datetime(2024, 1, i, tzinfo=timezone.utc) for i in range(1, 21)], name="ts"
    )
    df = pd.DataFrame({
        "open": [price] * 20, "high": [price * 1.02] * 20, "low": [price * 0.98] * 20,
        "close": [price] * 20, "volume": [1_000_000] * 20,
    }, index=idx)
    store.upsert_bars(df, "AAPL", "1Day", AssetClass.STOCK)
    return store


def _news_event(materiality=0.8, confidence=0.7, direction=1, tickers=("AAPL",)):
    item = NewsItem(
        id=NewsItem.compute_id("t", "u", datetime(2024, 1, 1, tzinfo=timezone.utc)),
        source=NewsSource.RSS, title="test", url="https://x", published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    analysis = NewsAnalysis(
        relevance=0.8, sentiment=Sentiment.POSITIVE, sentiment_score=0.7,
        materiality=materiality, catalyst_type=CatalystType.GUIDANCE,
        direction_hint=direction, confidence=confidence,
    )
    return NewsEvent(item=item, tickers=list(tickers), analysis=analysis)


def test_news_momentum_emits_when_thresholds_met(tmp_path):
    store = _bars(tmp_path)
    ctx = Context(bar_store=store, state=None)
    strat = NewsMomentumStrategy()
    signals = strat.on_news(_news_event(), ctx)
    assert len(signals) == 1
    assert signals[0].symbol == "AAPL"
    assert signals[0].side == SignalSide.LONG


def test_news_momentum_skips_low_materiality(tmp_path):
    store = _bars(tmp_path)
    ctx = Context(bar_store=store, state=None)
    strat = NewsMomentumStrategy(materiality_floor=0.8)
    signals = strat.on_news(_news_event(materiality=0.3), ctx)
    assert signals == []


def test_news_momentum_skips_when_no_analysis(tmp_path):
    store = _bars(tmp_path)
    ctx = Context(bar_store=store, state=None)
    strat = NewsMomentumStrategy()
    item = NewsItem(
        id="x", source=NewsSource.RSS, title="t", url="u",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    ev = NewsEvent(item=item, tickers=["AAPL"], analysis=None)
    assert strat.on_news(ev, ctx) == []


def _account(equity=10000.0):
    return Account(
        account_id="X", currency="USD",
        cash=Decimal(str(equity)), equity=Decimal(str(equity)),
        buying_power=Decimal(str(equity * 2)), portfolio_value=Decimal(str(equity)),
    )


def test_risk_sizes_long_signal(tmp_path):
    store = _bars(tmp_path, price=100.0)
    cfg = load_config(profile="paper")
    risk = RiskManager(cfg)
    sig = Signal(strategy="news_momentum", symbol="AAPL", asset_class=AssetClass.STOCK,
                 side=SignalSide.LONG, conviction=0.8, horizon_days=5)
    d = risk.evaluate(sig, account=_account(), positions=[], bar_store=store)
    assert d.ok
    assert d.order is not None
    assert d.order.symbol == "AAPL"
    assert d.order.quantity > 0


def test_risk_rejects_when_daily_halted(tmp_path):
    store = _bars(tmp_path)
    cfg = load_config(profile="paper")
    risk = RiskManager(cfg)
    risk.update_equity(10000.0)
    risk.update_equity(9700.0)  # 3% loss > 2% daily limit
    assert risk.circuits.daily_halted
    sig = Signal(strategy="s", symbol="AAPL", asset_class=AssetClass.STOCK,
                 side=SignalSide.LONG, conviction=0.8, horizon_days=5)
    d = risk.evaluate(sig, account=_account(9700.0), positions=[], bar_store=store)
    assert not d.ok
    assert "daily" in d.reason


def test_risk_rejects_when_position_cap_hit(tmp_path):
    store = _bars(tmp_path)
    cfg = load_config(profile="paper")
    risk = RiskManager(cfg)
    sig = Signal(strategy="s", symbol="AAPL", asset_class=AssetClass.STOCK,
                 side=SignalSide.LONG, conviction=0.5, horizon_days=5)
    # 8 stock positions = at max_per_asset_class
    positions = [
        Position(symbol=f"X{i}", asset_class=AssetClass.STOCK,
                 quantity=Decimal("1"), avg_entry_price=Decimal("10"))
        for i in range(8)
    ]
    d = risk.evaluate(sig, account=_account(), positions=positions, bar_store=store)
    assert not d.ok


def test_risk_rejects_doubling_existing_long(tmp_path):
    store = _bars(tmp_path)
    cfg = load_config(profile="paper")
    risk = RiskManager(cfg)
    sig = Signal(strategy="s", symbol="AAPL", asset_class=AssetClass.STOCK,
                 side=SignalSide.LONG, conviction=0.5, horizon_days=5)
    pos = [Position(symbol="AAPL", asset_class=AssetClass.STOCK,
                    quantity=Decimal("10"), avg_entry_price=Decimal("100"))]
    d = risk.evaluate(sig, account=_account(), positions=pos, bar_store=store)
    assert not d.ok
    assert "already" in d.reason


@pytest.mark.asyncio
async def test_signal_consumer_flow(tmp_path):
    store = _bars(tmp_path)
    cfg = load_config(profile="paper")
    risk = RiskManager(cfg)
    bus = EventBus(); bus.bind_loop()

    broker = MagicMock()
    broker.get_account = AsyncMock(return_value=_account())
    broker.get_positions = AsyncMock(return_value=[])
    broker.submit_order = AsyncMock(return_value=MagicMock(order_id="ORD1"))

    om = OrderManager(broker=broker, bus=bus)
    consumer = SignalConsumer(broker=broker, risk=risk, order_manager=om, bar_store=store, bus=bus, autosubmit=True)

    sig = Signal(strategy="news_momentum", symbol="AAPL", asset_class=AssetClass.STOCK,
                 side=SignalSide.LONG, conviction=0.8, horizon_days=5)
    outcome = await consumer._process(sig)
    assert outcome.decision.ok
    assert outcome.order_record is not None
    assert broker.submit_order.called


@pytest.mark.asyncio
async def test_signal_consumer_no_submit_when_disabled(tmp_path):
    store = _bars(tmp_path)
    cfg = load_config(profile="paper")
    risk = RiskManager(cfg)
    bus = EventBus(); bus.bind_loop()
    broker = MagicMock()
    broker.get_account = AsyncMock(return_value=_account())
    broker.get_positions = AsyncMock(return_value=[])
    broker.submit_order = AsyncMock()
    om = OrderManager(broker=broker, bus=bus)
    consumer = SignalConsumer(broker=broker, risk=risk, order_manager=om, bar_store=store, bus=bus, autosubmit=False)

    sig = Signal(strategy="news_momentum", symbol="AAPL", asset_class=AssetClass.STOCK,
                 side=SignalSide.LONG, conviction=0.8, horizon_days=5)
    outcome = await consumer._process(sig)
    assert outcome.decision.ok
    assert outcome.order_record is None
    assert not broker.submit_order.called
