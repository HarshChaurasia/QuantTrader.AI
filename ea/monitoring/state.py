"""Dashboard state aggregator.

Single place that the dashboard talks to. Pulls from broker + store on demand.
As later subsystems (event bus, news, signals, risk) come online, they'll publish
into this aggregator and the dashboard will surface them without re-plumbing.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ea.brokers.alpaca.client import AlpacaBroker
from ea.brokers.models import AssetClass, BarEvent
from ea.config import Config
from ea.data.store import BarStore
from ea.eventbus import get_bus
from ea.logging import logger
from ea.news.models import NewsEvent

DEFAULT_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]


@dataclass
class AlertItem:
    timestamp: datetime
    level: str  # "info" | "warning" | "danger"
    message: str


@dataclass
class DashboardState:
    """Mutable in-memory state. Single instance per process."""
    config: Config
    broker: AlpacaBroker
    store: BarStore
    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    alerts: deque[AlertItem] = field(default_factory=lambda: deque(maxlen=100))
    kill_switch_armed: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Live bar cache populated by AlpacaStreamRunner (Phase A.3)
    latest_bars: dict[str, BarEvent] = field(default_factory=dict)

    # News events (Phase A.4) — most recent first
    recent_news: deque[NewsEvent] = field(default_factory=lambda: deque(maxlen=50))

    # Placeholders for future-phase data
    recent_signals: list[dict] = field(default_factory=list)
    risk_breakers: dict[str, Any] = field(default_factory=dict)
    pnl_today: float = 0.0
    starting_equity_today: float | None = None

    def add_alert(self, level: str, message: str) -> None:
        self.alerts.appendleft(AlertItem(
            timestamp=datetime.now(timezone.utc), level=level, message=message
        ))
        logger.info("alert [{}]: {}", level, message)


_state: DashboardState | None = None


def init_state(config: Config) -> DashboardState:
    """Build the singleton state. Call once at server startup."""
    global _state
    if _state is None:
        broker = AlpacaBroker(config)
        store = BarStore()
        _state = DashboardState(config=config, broker=broker, store=store)
        _state.add_alert("info", "Dashboard initialized")
    return _state


def get_state() -> DashboardState:
    if _state is None:
        raise RuntimeError("Dashboard state not initialized — call init_state() first.")
    return _state


# --- Snapshot helpers used by HTTP routes ---

async def account_snapshot() -> dict:
    from ea.monitoring import equity_baseline
    s = get_state()
    acct = await s.broker.get_account()
    if s.starting_equity_today is None:
        existing = equity_baseline.load_today()
        if existing is not None:
            s.starting_equity_today = existing
        else:
            s.starting_equity_today = float(acct.equity)
            equity_baseline.save_today(s.starting_equity_today)
    pnl = float(acct.equity) - s.starting_equity_today
    pnl_pct = (pnl / s.starting_equity_today * 100) if s.starting_equity_today else 0.0
    return {
        "account_id": acct.account_id,
        "currency": acct.currency,
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "buying_power": float(acct.buying_power),
        "portfolio_value": float(acct.portfolio_value),
        "pattern_day_trader": acct.pattern_day_trader,
        "trading_blocked": acct.trading_blocked,
        "account_blocked": acct.account_blocked,
        "pnl_today": pnl,
        "pnl_today_pct": pnl_pct,
    }


async def positions_snapshot() -> list[dict]:
    s = get_state()
    positions = await s.broker.get_positions()
    return [
        {
            "symbol": p.symbol,
            "asset_class": p.asset_class.value,
            "quantity": float(p.quantity),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price) if p.current_price else None,
            "market_value": float(p.market_value) if p.market_value else None,
            "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
            "unrealized_pl_pct": p.unrealized_pl_pct,
        }
        for p in positions
    ]


def system_snapshot() -> dict:
    s = get_state()
    return {
        "profile": s.config.profile.profile,
        "is_live": s.config.is_live,
        "broker": s.broker.name,
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": (datetime.now(timezone.utc) - s.started_at).total_seconds(),
        "store_rows": s.store.row_count(),
        "store_stock_symbols": len(s.store.list_symbols(AssetClass.STOCK)),
        "store_crypto_symbols": len(s.store.list_symbols(AssetClass.CRYPTO)),
        "kill_switch_armed": s.kill_switch_armed,
        "alerts_count": len(s.alerts),
    }


def alerts_snapshot(limit: int = 20) -> list[dict]:
    s = get_state()
    return [
        {"timestamp": a.timestamp.isoformat(), "level": a.level, "message": a.message}
        for a in list(s.alerts)[:limit]
    ]


def _asset_class_for(symbol: str) -> AssetClass:
    return AssetClass.CRYPTO if "/" in symbol else AssetClass.STOCK


def watchlist_snapshot() -> list[dict]:
    """Watchlist with last/change from live bars if available, else from store."""
    s = get_state()
    out: list[dict] = []
    for sym in s.watchlist:
        ac = _asset_class_for(sym)
        df = s.store.get_bars(sym, "1Day", ac)
        live = s.latest_bars.get(sym)
        if df.empty and live is None:
            out.append({"symbol": sym, "last": None, "change_pct": None, "bars": 0, "live": False})
            continue
        prev = None
        if not df.empty:
            prev = float(df["close"].iloc[-1])
        last = float(live.close) if live is not None else prev
        if prev is None:
            prev = last
        change_pct = (last - prev) / prev * 100 if prev else 0.0
        out.append({
            "symbol": sym,
            "last": last,
            "change_pct": change_pct,
            "bars": int(len(df)) if not df.empty else 0,
            "live": live is not None,
        })
    return out


def chart_data(symbol: str, limit: int = 200) -> dict:
    s = get_state()
    df = s.store.get_bars(symbol, "1Day", _asset_class_for(symbol))
    if df.empty:
        return {"symbol": symbol, "bars": []}
    df_tail = df.tail(limit)
    bars = [
        {
            "time": int(ts.timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        for ts, row in df_tail.iterrows()
    ]
    return {"symbol": symbol, "bars": bars}


# --- Mutations ---

def add_to_watchlist(symbol: str) -> list[str]:
    s = get_state()
    symbol = symbol.upper().strip()
    if symbol and symbol not in s.watchlist:
        s.watchlist.append(symbol)
        s.add_alert("info", f"Watchlist: added {symbol}")
    return list(s.watchlist)


def remove_from_watchlist(symbol: str) -> list[str]:
    s = get_state()
    symbol = symbol.upper().strip()
    if symbol in s.watchlist:
        s.watchlist.remove(symbol)
        s.add_alert("info", f"Watchlist: removed {symbol}")
    return list(s.watchlist)


def arm_kill_switch() -> dict:
    """Phase A.2 stub: arm the kill switch. Actual order cancellation lands in T A.11."""
    s = get_state()
    s.kill_switch_armed = True
    s.add_alert("danger", "KILL SWITCH ARMED — would cancel all orders & close all positions (Phase A.11 wires real action)")
    return {"armed": True, "message": "Kill switch armed (stub — actions implemented in T A.11)"}


def disarm_kill_switch() -> dict:
    s = get_state()
    s.kill_switch_armed = False
    s.add_alert("info", "Kill switch disarmed")
    return {"armed": False}


# --- Event bus integration (Phase A.3) ---

async def consume_bus(stop_evt: asyncio.Event | None = None) -> None:
    """Long-running task: subscribe to the bus, cache bars + news, persist live bars."""
    import pandas as pd

    bus = get_bus()
    bus.bind_loop()
    q = bus.subscribe(maxsize=500)
    s = get_state()
    try:
        while True:
            if stop_evt is not None and stop_evt.is_set():
                break
            event = await q.get()
            if isinstance(event, BarEvent):
                s.latest_bars[event.symbol] = event
                # Persist to store (idempotent upsert handles re-arrivals)
                try:
                    df = pd.DataFrame(
                        {
                            "open": [float(event.open)], "high": [float(event.high)],
                            "low": [float(event.low)], "close": [float(event.close)],
                            "volume": [float(event.volume)],
                        },
                        index=pd.DatetimeIndex([event.timestamp], name="ts"),
                    )
                    s.store.upsert_bars(df, event.symbol, event.timeframe, event.asset_class, source="stream")
                except Exception as e:
                    logger.debug("bar persist failed: {}", e)
            elif isinstance(event, NewsEvent):
                s.recent_news.appendleft(event)
    finally:
        bus.unsubscribe(q)


def news_snapshot(limit: int = 20) -> list[dict]:
    s = get_state()
    out: list[dict] = []
    for ev in list(s.recent_news)[:limit]:
        out.append({
            "id": ev.item.id,
            "source": ev.item.source.value,
            "source_name": ev.item.raw_source_name,
            "title": ev.item.title,
            "url": ev.item.url,
            "published_at": ev.item.published_at.isoformat(),
            "summary": ev.item.summary,
            "tickers": ev.tickers,
            "has_analysis": ev.has_analysis,
            "analysis": ev.analysis.model_dump(mode="json") if ev.analysis else None,
        })
    return out


def news_event_payload(ev: NewsEvent) -> dict:
    return {
        "id": ev.item.id,
        "source": ev.item.source.value,
        "source_name": ev.item.raw_source_name,
        "title": ev.item.title,
        "url": ev.item.url,
        "published_at": ev.item.published_at.isoformat(),
        "tickers": ev.tickers,
        "has_analysis": ev.has_analysis,
    }


def latest_bar_payload(event: BarEvent) -> dict:
    """Serialize a BarEvent for SSE / JSON."""
    return {
        "symbol": event.symbol,
        "asset_class": event.asset_class.value,
        "timestamp": event.timestamp.isoformat(),
        "time": int(event.timestamp.timestamp()),
        "open": float(event.open),
        "high": float(event.high),
        "low": float(event.low),
        "close": float(event.close),
        "volume": float(event.volume),
        "timeframe": event.timeframe,
    }
