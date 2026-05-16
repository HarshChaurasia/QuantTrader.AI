"""FastAPI dashboard server — single entry point for the paper trading system.

Orchestrates the full stack at startup: bus consumer, news poller, strategy runner,
signal consumer (risk + order manager).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ea.brokers.alpaca.client import AlpacaBroker
from ea.brokers.models import AssetClass, BarEvent
from ea.config import get_config
from ea.data.store import BarStore
from ea.eventbus import get_bus
from ea.execution.order_manager import OrderManager
from ea.execution.signal_consumer import SignalConsumer
from ea.logging import logger, setup_logging
from ea.monitoring import state as state_mod
from ea.news.analyzer import NewsAnalyzer
from ea.news.models import NewsEvent
from ea.news.poller import NewsPoller
from ea.risk.manager import RiskManager
from ea.strategies.base import Context
from ea.scanner.smc_scanner import SMCScanner
from ea.strategies.news_momentum import NewsMomentumStrategy
from ea.strategies.runner import StrategyRunner
from ea.strategies.smc.strategy import SMCStrategy
from ea.strategies.smc.scalp import SMCScalpStrategy
from ea.strategies.xsection_momentum import CrossSectionalMomentumStrategy

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"


class WatchlistAdd(BaseModel):
    symbol: str


class OrderRequestModel(BaseModel):
    symbol: str
    side: str
    quantity: float
    order_type: str = "market"
    limit_price: float | None = None


class StreamStartRequest(BaseModel):
    asset_class: str = "crypto"
    symbols: list[str] | None = None


# Process-wide subsystems
_STREAMS: dict[str, Any] = {}
_NEWS_POLLER: NewsPoller | None = None
_STRATEGY_RUNNER: StrategyRunner | None = None
_SIGNAL_CONSUMER: SignalConsumer | None = None
_RISK: RiskManager | None = None
_ORDER_MGR: OrderManager | None = None
_SMC_SCANNER: SMCScanner | None = None

# Default SMC scan universes — user can override at runtime via /api/scanner/universe
DEFAULT_SCAN_STOCKS = [
    # Indices / ETFs
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "SMH", "ARKK",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "AVGO", "ORCL",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA",
    # Consumer / retail
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "DIS",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE", "ABBV",
    # Energy / industrials
    "XOM", "CVX", "BA", "CAT", "GE",
    # High-volatility / momentum names
    "COIN", "PLTR", "SHOP", "NFLX", "UBER", "SNOW",
]
DEFAULT_SCAN_CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"]
DEFAULT_SCAN_FOREX = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD"]

# 1-minute scalping universe (crypto + forex only — no PDT, see SMCScalpStrategy).
# Kept small: 1Min data + intraday signal rate is heavy.
DEFAULT_SCALP_CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD"]
DEFAULT_SCALP_FOREX = ["EURUSD", "GBPUSD", "USDJPY"]

_SCALP_FOREX_TASK: asyncio.Task | None = None
_ALERT_MONITOR: Any | None = None


def create_app(*, autosubmit: bool = False) -> FastAPI:
    setup_logging()
    config = get_config()
    state_mod.init_state(config)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        global _NEWS_POLLER, _STRATEGY_RUNNER, _SIGNAL_CONSUMER, _RISK, _ORDER_MGR, _SMC_SCANNER
        global _ALERT_MONITOR
        bus = get_bus()
        bus.bind_loop()

        # Risk + order manager
        s = state_mod.get_state()
        _RISK = RiskManager(config, bus=bus)
        _ORDER_MGR = OrderManager(broker=s.broker, bus=bus)

        # Strategy runner — register active strategies here
        ctx = Context(bar_store=s.store, state=s)
        _STRATEGY_RUNNER = StrategyRunner(
            [
                NewsMomentumStrategy(),
                CrossSectionalMomentumStrategy(),
                SMCStrategy(),
                SMCScalpStrategy(),  # parallel 1Min crypto+forex scalper
            ],
            ctx=ctx, bus=bus,
        )
        _STRATEGY_RUNNER.start()

        # Signal consumer wires strategies → risk → order
        _SIGNAL_CONSUMER = SignalConsumer(
            broker=s.broker, risk=_RISK, order_manager=_ORDER_MGR,
            bar_store=s.store, bus=bus, autosubmit=autosubmit,
        )
        _SIGNAL_CONSUMER.start()

        # Bus consumer for dashboard state (bars/news caches)
        consumer = asyncio.create_task(state_mod.consume_bus(), name="bus-consumer")

        # News poller (with LLM analyzer if API key present)
        analyzer = NewsAnalyzer(config)
        _NEWS_POLLER = NewsPoller(
            bus=bus,
            watchlist_provider=lambda: list(state_mod.get_state().watchlist),
            analyzer=analyzer,
        )
        _NEWS_POLLER.start()

        # SMC scanner with default universes
        _SMC_SCANNER = SMCScanner(s.store, scan_interval_s=300.0, timeframe="1Hour")
        _SMC_SCANNER.set_universes(
            stocks=DEFAULT_SCAN_STOCKS,
            crypto=DEFAULT_SCAN_CRYPTO,
            forex=DEFAULT_SCAN_FOREX,
        )
        _SMC_SCANNER.start()

        # Auto-start live streams for watchlist symbols
        from ea.brokers.alpaca.stream import AlpacaStreamRunner
        stock_syms = [sym for sym in s.watchlist if "/" not in sym]
        # Union scalp crypto symbols into the crypto stream so the 1Min
        # scalper gets live bars even if they aren't on the watchlist.
        crypto_syms = sorted({sym for sym in s.watchlist if "/" in sym} | set(DEFAULT_SCALP_CRYPTO))
        if stock_syms:
            try:
                runner = AlpacaStreamRunner(config, stock_syms, asset_class=AssetClass.STOCK, bus=bus)
                runner.start()
                _STREAMS["stock"] = runner
                s.add_alert("info", f"Stock stream started: {','.join(stock_syms)}")
            except Exception as e:
                s.add_alert("warning", f"Stock stream failed to start: {e}")
        if crypto_syms:
            try:
                runner = AlpacaStreamRunner(config, crypto_syms, asset_class=AssetClass.CRYPTO, bus=bus)
                runner.start()
                _STREAMS["crypto"] = runner
                s.add_alert("info", f"Crypto stream started: {','.join(crypto_syms)}")
            except Exception as e:
                s.add_alert("warning", f"Crypto stream failed to start: {e}")

        # --- 1Min scalping data feeds ---
        global _SCALP_FOREX_TASK
        from ea.data.backfill import backfill_symbols, _fetch_from_yfinance
        from datetime import timedelta
        from decimal import Decimal

        # Cold-start 1Min history for scalp crypto (Alpaca supports crypto 1Min)
        # so evaluate_setup has its >=30-bar window immediately, not after an
        # hour of live ticks. Best-effort; live stream tops it up via consume_bus.
        async def _scalp_crypto_backfill() -> None:
            try:
                start = datetime.now(timezone.utc) - timedelta(days=2)
                await backfill_symbols(
                    config, s.store, DEFAULT_SCALP_CRYPTO,
                    start=start, timeframe="1Min",
                    asset_class=AssetClass.CRYPTO, concurrency=2,
                )
                s.add_alert("info", f"Scalp 1Min backfill: {','.join(DEFAULT_SCALP_CRYPTO)}")
            except Exception as e:
                s.add_alert("warning", f"Scalp crypto backfill failed: {e}")

        # Forex has no Alpaca stream and the backfill helper blocks 1Min
        # yfinance — so poll yfinance 1Min directly every 60s, upsert, and
        # republish the latest bar onto the bus for the scalper. Signal-only:
        # forex still has no live execution path (Phase C / OANDA).
        async def _scalp_forex_poller() -> None:
            while True:
                for sym in DEFAULT_SCALP_FOREX:
                    try:
                        start = datetime.now(timezone.utc) - timedelta(days=2)
                        end = datetime.now(timezone.utc)
                        df = await asyncio.to_thread(
                            _fetch_from_yfinance, sym, start, end, AssetClass.FOREX, "1Min",
                        )
                        if df is None or df.empty:
                            continue
                        df = df.dropna(subset=["open", "high", "low", "close"])
                        if df.empty:
                            continue
                        s.store.upsert_bars(df, sym, "1Min", AssetClass.FOREX, source="yfinance")
                        last = df.iloc[-1]
                        await bus.publish(BarEvent(
                            symbol=sym, asset_class=AssetClass.FOREX,
                            timestamp=df.index[-1].to_pydatetime(),
                            open=Decimal(str(last["open"])), high=Decimal(str(last["high"])),
                            low=Decimal(str(last["low"])), close=Decimal(str(last["close"])),
                            volume=Decimal(str(last.get("volume", 0) or 0)), timeframe="1Min",
                        ))
                    except Exception as e:
                        logger.debug("scalp forex poll {} failed: {}", sym, e)
                await asyncio.sleep(60)

        asyncio.create_task(_scalp_crypto_backfill(), name="scalp-crypto-backfill")
        _SCALP_FOREX_TASK = asyncio.create_task(_scalp_forex_poller(), name="scalp-forex-poller")
        s.add_alert(
            "info",
            f"Scalper armed · {len(DEFAULT_SCALP_CRYPTO)} crypto + {len(DEFAULT_SCALP_FOREX)} fx @1Min",
        )

        # Operational alert monitor (breaker / disconnect / unfilled-order)
        from ea.monitoring.alerts import AlertMonitor
        _ALERT_MONITOR = AlertMonitor(
            s, risk=_RISK, streams=_STREAMS, order_mgr=_ORDER_MGR, interval_s=60.0,
        )
        _ALERT_MONITOR.start()

        s.add_alert(
            "info",
            f"Stack started · autosubmit={autosubmit} · LLM={'on' if analyzer.enabled else 'off'}",
        )

        try:
            yield
        finally:
            if _SMC_SCANNER is not None:
                await _SMC_SCANNER.stop(); _SMC_SCANNER = None
            if _NEWS_POLLER is not None:
                await _NEWS_POLLER.stop(); _NEWS_POLLER = None
            if _SIGNAL_CONSUMER is not None:
                await _SIGNAL_CONSUMER.stop(); _SIGNAL_CONSUMER = None
            if _STRATEGY_RUNNER is not None:
                await _STRATEGY_RUNNER.stop(); _STRATEGY_RUNNER = None
            if _ALERT_MONITOR is not None:
                await _ALERT_MONITOR.stop(); _ALERT_MONITOR = None
            if _SCALP_FOREX_TASK is not None:
                _SCALP_FOREX_TASK.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await _SCALP_FOREX_TASK
                _SCALP_FOREX_TASK = None
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer
            for runner in list(_STREAMS.values()):
                with contextlib.suppress(Exception):
                    runner.stop()
            _STREAMS.clear()

    app = FastAPI(title="EA Dashboard", version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        s = state_mod.get_state()
        return templates.TemplateResponse(
            request, "index.html",
            {"profile": s.config.profile.profile, "is_live": s.config.is_live},
        )

    @app.get("/api/account")
    async def api_account():
        try:
            return await state_mod.account_snapshot()
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/positions")
    async def api_positions():
        try:
            return await state_mod.positions_snapshot()
        except Exception:
            return []

    @app.get("/api/system")
    async def api_system():
        sys = state_mod.system_snapshot()
        sys["streams"] = {k: v.status() for k, v in _STREAMS.items()}
        sys["news_poller"] = _NEWS_POLLER.status() if _NEWS_POLLER else {"running": False}
        sys["strategy_runner"] = _STRATEGY_RUNNER.status() if _STRATEGY_RUNNER else []
        sys["risk"] = _RISK.snapshot() if _RISK else {}
        sys["order_mgr"] = _ORDER_MGR.status() if _ORDER_MGR else {}
        sys["autosubmit"] = _SIGNAL_CONSUMER.autosubmit if _SIGNAL_CONSUMER else False
        sys["scanner"] = _SMC_SCANNER.status() if _SMC_SCANNER else {"running": False}
        return sys

    @app.get("/api/alerts")
    async def api_alerts(limit: int = 20):
        return state_mod.alerts_snapshot(limit=limit)

    @app.get("/api/watchlist")
    async def api_watchlist():
        return state_mod.watchlist_snapshot()

    @app.post("/api/watchlist")
    async def api_watchlist_add(body: WatchlistAdd):
        return {"watchlist": state_mod.add_to_watchlist(body.symbol)}

    @app.delete("/api/watchlist/{symbol}")
    async def api_watchlist_remove(symbol: str):
        return {"watchlist": state_mod.remove_from_watchlist(symbol)}

    @app.get("/api/chart/{symbol}")
    async def api_chart(symbol: str, limit: int = 200):
        data = state_mod.chart_data(symbol.upper(), limit=limit)
        if not data["bars"]:
            raise HTTPException(404, f"no bars for {symbol}")
        return data

    @app.post("/api/kill")
    async def api_kill():
        result = state_mod.arm_kill_switch()
        # Actually flatten: cancel all + close positions
        try:
            n = await state_mod.get_state().broker.close_all_positions()
            result["closed"] = n
            if _SIGNAL_CONSUMER is not None:
                _SIGNAL_CONSUMER.set_autosubmit(False)
            state_mod.get_state().add_alert("danger", f"KILL EXECUTED: {n} positions closed; autosubmit OFF")
        except Exception as e:
            state_mod.get_state().add_alert("danger", f"kill switch error: {e}")
            result["error"] = str(e)
        return result

    @app.post("/api/kill/disarm")
    async def api_kill_disarm():
        return state_mod.disarm_kill_switch()

    @app.post("/api/order")
    async def api_order(order: OrderRequestModel):
        if _ORDER_MGR is None:
            raise HTTPException(503, "order manager not initialized")
        from decimal import Decimal as D
        import uuid
        from ea.brokers.models import OrderRequest as OR, OrderSide as OS, OrderType as OT
        try:
            ac = AssetClass.CRYPTO if "/" in order.symbol else AssetClass.STOCK
            req = OR(
                symbol=order.symbol.upper(), asset_class=ac,
                side=OS.BUY if order.side.lower() == "buy" else OS.SELL,
                quantity=D(str(order.quantity)),
                order_type=OT.LIMIT if order.order_type == "limit" else OT.MARKET,
                limit_price=D(str(order.limit_price)) if order.limit_price else None,
                client_order_id=f"ea-manual-{uuid.uuid4().hex[:10]}",
            )
            record = await _ORDER_MGR.submit(req)
            if record.order is not None:
                return {"ok": True, "order_id": record.order.order_id, "status": record.order.status.value}
            return {"ok": False, "error": record.error}
        except Exception as e:
            raise HTTPException(400, f"order rejected: {e}")

    @app.post("/api/autosubmit")
    async def api_autosubmit(enabled: bool):
        if _SIGNAL_CONSUMER is None:
            raise HTTPException(503, "signal consumer not running")
        _SIGNAL_CONSUMER.set_autosubmit(enabled)
        state_mod.get_state().add_alert("info", f"autosubmit -> {'ON' if enabled else 'OFF'}")
        return {"autosubmit": enabled}

    @app.get("/api/signals")
    async def api_signals(limit: int = 20):
        if _SIGNAL_CONSUMER is None:
            return []
        out = []
        for o in _SIGNAL_CONSUMER.recent[:limit]:
            out.append({
                "timestamp": o.timestamp.isoformat(),
                "strategy": o.signal.strategy,
                "symbol": o.signal.symbol,
                "side": o.signal.side.value,
                "conviction": o.signal.conviction,
                "horizon_days": o.signal.horizon_days,
                "rationale": o.signal.rationale,
                "accepted": o.decision.ok,
                "reason": o.decision.reason,
                "submitted": o.order_record is not None and o.order_record.order is not None,
                "order_status": (
                    o.order_record.order.status.value
                    if o.order_record and o.order_record.order else None
                ),
            })
        return out

    @app.get("/api/orders")
    async def api_orders(include_broker_open: bool = True):
        if _ORDER_MGR is None:
            return []
        rows = [
            {
                "order_id": r.order.order_id if r.order else None,
                "client_order_id": r.request.client_order_id,
                "symbol": r.request.symbol,
                "side": r.request.side.value,
                "quantity": float(r.request.quantity),
                "order_type": r.request.order_type.value,
                "limit_price": float(r.request.limit_price) if r.request.limit_price else None,
                "status": r.order.status.value if r.order else "failed",
                "filled_qty": float(r.order.filled_quantity) if r.order else 0,
                "avg_fill_price": float(r.order.avg_fill_price) if r.order and r.order.avg_fill_price else None,
                "submitted_at": r.submitted_at.isoformat(),
                "error": r.error,
                "source": "local",
            }
            for r in _ORDER_MGR.recent[:50]
        ]
        # Also include any open orders sitting at the broker that we didn't submit ourselves
        if include_broker_open:
            try:
                broker_open = await state_mod.get_state().broker.list_open_orders()
                seen_ids = {r["client_order_id"] for r in rows}
                for o in broker_open:
                    if o.client_order_id and o.client_order_id in seen_ids:
                        continue
                    rows.append({
                        "order_id": o.order_id,
                        "client_order_id": o.client_order_id,
                        "symbol": o.symbol,
                        "side": o.side.value,
                        "quantity": float(o.quantity),
                        "order_type": o.order_type.value,
                        "limit_price": None,
                        "status": o.status.value,
                        "filled_qty": float(o.filled_quantity),
                        "avg_fill_price": float(o.avg_fill_price) if o.avg_fill_price else None,
                        "submitted_at": o.submitted_at.isoformat(),
                        "error": None,
                        "source": "broker",
                    })
            except Exception as e:
                logger.debug("list_open_orders failed: {}", e)
        return rows

    @app.delete("/api/orders/{order_id}")
    async def api_cancel_order(order_id: str):
        try:
            await state_mod.get_state().broker.cancel_order(order_id)
            state_mod.get_state().add_alert("info", f"order canceled: {order_id}")
            return {"canceled": order_id}
        except Exception as e:
            raise HTTPException(400, f"cancel failed: {e}")

    @app.get("/api/risk")
    async def api_risk():
        if _RISK is None:
            return {}
        return _RISK.snapshot()

    @app.get("/api/strategies")
    async def api_strategies():
        if _STRATEGY_RUNNER is None:
            return []
        return _STRATEGY_RUNNER.status()

    @app.post("/api/strategies/{name}/pause")
    async def api_strategy_pause(name: str):
        if _STRATEGY_RUNNER is None or not _STRATEGY_RUNNER.pause(name):
            raise HTTPException(404, f"strategy {name} not found")
        state_mod.get_state().add_alert("info", f"strategy paused: {name}")
        return {"name": name, "enabled": False}

    @app.post("/api/strategies/{name}/resume")
    async def api_strategy_resume(name: str):
        if _STRATEGY_RUNNER is None or not _STRATEGY_RUNNER.resume(name):
            raise HTTPException(404, f"strategy {name} not found")
        state_mod.get_state().add_alert("info", f"strategy resumed: {name}")
        return {"name": name, "enabled": True}

    @app.get("/api/scalp")
    async def api_scalp(limit: int = 30):
        """Scalping-tab payload: smc_scalp signals + open scalp positions.

        Full cross-strategy history still lives in the Signals/Orders/Positions
        panels — this is a filtered focus view, not a separate ledger.
        """
        scalp_universe = set(DEFAULT_SCALP_CRYPTO) | set(DEFAULT_SCALP_FOREX)
        enabled = _STRATEGY_RUNNER.is_enabled("smc_scalp") if _STRATEGY_RUNNER else False

        signals: list[dict] = []
        scalp_syms: set[str] = set()
        if _SIGNAL_CONSUMER is not None:
            for o in _SIGNAL_CONSUMER.recent:
                if o.signal.strategy != "smc_scalp":
                    continue
                scalp_syms.add(o.signal.symbol)
                signals.append({
                    "timestamp": o.timestamp.isoformat(),
                    "symbol": o.signal.symbol,
                    "side": o.signal.side.value,
                    "conviction": o.signal.conviction,
                    "rationale": o.signal.rationale,
                    "accepted": o.decision.ok,
                    "reason": o.decision.reason,
                    "submitted": o.order_record is not None and o.order_record.order is not None,
                    "order_status": (
                        o.order_record.order.status.value
                        if o.order_record and o.order_record.order else None
                    ),
                })
                if len(signals) >= limit:
                    break

        positions: list[dict] = []
        try:
            for p in await state_mod.positions_snapshot():
                if p.get("symbol") in scalp_universe or p.get("symbol") in scalp_syms:
                    positions.append(p)
        except Exception:
            pass

        return {
            "enabled": enabled,
            "universe": {"crypto": DEFAULT_SCALP_CRYPTO, "forex": DEFAULT_SCALP_FOREX},
            "timeframe": "1Min",
            "signals": signals,
            "positions": positions,
            "signal_count": len(signals),
        }

    @app.post("/api/report/eod")
    async def api_report_eod():
        from ea.monitoring.reports import write_eod_report
        try:
            path = await write_eod_report(
                order_mgr=_ORDER_MGR, signal_consumer=_SIGNAL_CONSUMER, risk=_RISK,
            )
            state_mod.get_state().add_alert("info", f"EOD report written: {path.name}")
            return {"written": str(path)}
        except Exception as e:
            raise HTTPException(500, f"eod report failed: {e}")

    # --- Scanner endpoints ---

    @app.get("/api/scanner/results")
    async def api_scanner_results(
        status_filter: str | None = None,
        asset_class: str | None = None,
    ):
        if _SMC_SCANNER is None:
            return []
        rows = []
        for r in _SMC_SCANNER.results:
            if not r.has_setup:
                continue
            if status_filter and r.setup["status"] != status_filter:
                continue
            if asset_class and r.asset_class.value != asset_class:
                continue
            rows.append({
                "symbol": r.symbol,
                "asset_class": r.asset_class.value,
                "bars": r.bars_in_store,
                "scanned_at": r.scanned_at.isoformat(),
                "current_price": r.setup["current_price"],
                "side": r.setup["side"],
                "zone_kind": r.setup["zone_kind"],
                "entry_low": r.setup["entry_low"],
                "entry_high": r.setup["entry_high"],
                "stop": r.setup["stop"],
                "target": r.setup["target"],
                "risk_reward": r.setup["risk_reward"],
                "distance_pct": r.setup["distance_pct"],
                "status": r.setup["status"],
                "confluence": r.setup["confluence"],
            })
        # Sort: in_zone first, then approaching by distance, then watching
        priority = {"in_zone": 0, "approaching": 1, "watching": 2}
        rows.sort(key=lambda r: (priority.get(r["status"], 9), abs(r["distance_pct"])))
        return rows

    @app.get("/api/scanner/status")
    async def api_scanner_status():
        if _SMC_SCANNER is None:
            return {"running": False}
        return _SMC_SCANNER.status()

    @app.post("/api/scanner/scan-now")
    async def api_scanner_scan_now():
        if _SMC_SCANNER is None:
            raise HTTPException(503, "scanner not running")
        n = await _SMC_SCANNER.scan_once()
        return {"scanned": n, "status": _SMC_SCANNER.status()}

    @app.post("/api/scanner/universe")
    async def api_scanner_universe(stocks: list[str] | None = None,
                                   crypto: list[str] | None = None,
                                   forex: list[str] | None = None):
        if _SMC_SCANNER is None:
            raise HTTPException(503, "scanner not running")
        _SMC_SCANNER.set_universes(stocks=stocks, crypto=crypto, forex=forex)
        return _SMC_SCANNER.status()

    @app.post("/api/scanner/mode")
    async def api_scanner_mode(mode: str):
        if _SMC_SCANNER is None:
            raise HTTPException(503, "scanner not running")
        try:
            _SMC_SCANNER.set_mode(mode)
        except ValueError as e:
            raise HTTPException(400, str(e))
        state_mod.get_state().add_alert("info", f"Scanner mode -> {mode}")
        return _SMC_SCANNER.status()

    @app.get("/api/news")
    async def api_news(limit: int = 20):
        return state_mod.news_snapshot(limit=limit)

    @app.post("/api/news/poll")
    async def api_news_poll_now():
        if _NEWS_POLLER is None:
            raise HTTPException(503, "news poller not running")
        e = await _NEWS_POLLER.poll_edgar_once()
        r = await _NEWS_POLLER.poll_rss_once()
        y = await _NEWS_POLLER.poll_yahoo_once()
        return {"edgar_new": e, "rss_new": r, "yahoo_new": y}

    @app.post("/api/stream/start")
    async def api_stream_start(req: StreamStartRequest):
        from ea.brokers.alpaca.stream import AlpacaStreamRunner

        try:
            ac = AssetClass(req.asset_class.lower())
        except ValueError as e:
            raise HTTPException(400, f"invalid asset_class: {e}")
        if ac not in (AssetClass.STOCK, AssetClass.CRYPTO):
            raise HTTPException(400, f"asset_class {ac.value} not supported")

        symbols = req.symbols or state_mod.get_state().watchlist
        if not symbols:
            raise HTTPException(400, "no symbols to subscribe")

        old = _STREAMS.get(ac.value)
        if old is not None:
            old.stop()
        runner = AlpacaStreamRunner(get_config(), symbols, asset_class=ac)
        runner.start()
        _STREAMS[ac.value] = runner
        state_mod.get_state().add_alert("info", f"Stream started: {ac.value} · {','.join(symbols)}")
        return runner.status()

    @app.post("/api/stream/stop")
    async def api_stream_stop(asset_class: str = "crypto"):
        runner = _STREAMS.get(asset_class.lower())
        if runner is None:
            raise HTTPException(404, f"no running stream for {asset_class}")
        runner.stop()
        del _STREAMS[asset_class.lower()]
        state_mod.get_state().add_alert("info", f"Stream stopped: {asset_class}")
        return {"stopped": asset_class}

    @app.get("/api/stream/status")
    async def api_stream_status():
        return {k: v.status() for k, v in _STREAMS.items()}

    @app.get("/api/stream")
    async def api_stream(request: Request):
        bus = get_bus()
        bus.bind_loop()
        bar_q = bus.subscribe(maxsize=200)

        async def event_gen():
            tick = 0
            last_snapshot = 0.0
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    now = asyncio.get_event_loop().time()
                    if now - last_snapshot >= 2.0:
                        tick += 1
                        last_snapshot = now
                        payload: dict[str, Any] = {
                            "tick": tick,
                            "system": (lambda s: (s.update(
                                {"streams": {k: v.status() for k, v in _STREAMS.items()}}
                            ) or s))(state_mod.system_snapshot()),
                        }
                        try:
                            payload["account"] = await state_mod.account_snapshot()
                        except Exception:
                            payload["account"] = None
                        yield {"event": "snapshot", "data": json.dumps(payload)}

                    try:
                        event = await asyncio.wait_for(bar_q.get(), timeout=0.5)
                        if isinstance(event, BarEvent):
                            yield {
                                "event": "bar",
                                "data": json.dumps(state_mod.latest_bar_payload(event)),
                            }
                        elif isinstance(event, NewsEvent):
                            yield {
                                "event": "news",
                                "data": json.dumps(state_mod.news_event_payload(event)),
                            }
                    except asyncio.TimeoutError:
                        pass
            finally:
                bus.unsubscribe(bar_q)

        return EventSourceResponse(event_gen())

    @app.get("/api/healthz")
    async def healthz():
        return {"ok": True}

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, autosubmit: bool = False) -> None:
    import uvicorn

    app = create_app(autosubmit=autosubmit)
    uvicorn.run(app, host=host, port=port, log_level="info")
