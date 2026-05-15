"""Alpaca WebSocket stream wrapper.

Subscribes to bar updates for a set of symbols and publishes normalized `BarEvent`s
to the process-wide event bus.

alpaca-py's `*DataStream` classes run their own asyncio loop inside `.run()`. We
host that in a background thread and bridge events back to our loop via
`EventBus.publish_threadsafe`. This avoids the SDK's loop colliding with ours
(uvicorn, FastAPI) and keeps the integration clean.

Reconnection: alpaca-py handles auto-reconnect internally. If the underlying thread
dies, the runner logs + restarts with capped exponential backoff.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ea.brokers.models import AssetClass, BarEvent
from ea.config import Config
from ea.eventbus import EventBus, get_bus
from ea.logging import logger


def _to_decimal(v: Any) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _normalize_bar(raw: Any, asset_class: AssetClass, timeframe: str = "1Min") -> BarEvent:
    """Translate an alpaca-py Bar object into our BarEvent."""
    ts = getattr(raw, "timestamp", None)
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    elif ts is None:
        ts = datetime.now(timezone.utc)
    return BarEvent(
        symbol=str(raw.symbol),
        asset_class=asset_class,
        timestamp=ts,
        open=_to_decimal(raw.open),
        high=_to_decimal(raw.high),
        low=_to_decimal(raw.low),
        close=_to_decimal(raw.close),
        volume=_to_decimal(raw.volume),
        timeframe=timeframe,
    )


class AlpacaStreamRunner:
    """Runs an Alpaca stock or crypto data stream in a thread; bridges bars to the bus."""

    def __init__(
        self,
        config: Config,
        symbols: list[str],
        asset_class: AssetClass = AssetClass.STOCK,
        bus: EventBus | None = None,
    ):
        if asset_class not in (AssetClass.STOCK, AssetClass.CRYPTO):
            raise ValueError(f"Alpaca stream supports stock/crypto only, got {asset_class}")
        if config.env.alpaca_key_id is None or config.env.alpaca_secret_key is None:
            raise RuntimeError("Alpaca credentials missing.")
        self._config = config
        self._symbols = [s.strip().upper() for s in symbols if s.strip()]
        self._asset_class = asset_class
        self._bus = bus or get_bus()
        self._thread: threading.Thread | None = None
        self._stream: Any = None
        self._stop_evt = threading.Event()
        self._started_at: datetime | None = None

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def asset_class(self) -> AssetClass:
        return self._asset_class

    def _build_stream(self) -> Any:
        """Construct the alpaca-py stream object. Imported lazily so tests can run
        without network."""
        from alpaca.data.live.crypto import CryptoDataStream
        from alpaca.data.live.stock import StockDataStream

        env = self._config.env
        key = env.alpaca_key_id.get_secret_value()
        sec = env.alpaca_secret_key.get_secret_value()

        if self._asset_class == AssetClass.STOCK:
            from alpaca.data.enums import DataFeed
            feed_str = self._config.profile.broker.alpaca.data_feed.lower()
            feed = DataFeed.IEX if feed_str == "iex" else DataFeed.SIP
            return StockDataStream(api_key=key, secret_key=sec, feed=feed)
        return CryptoDataStream(api_key=key, secret_key=sec)

    async def _handle_bar(self, raw: Any) -> None:
        """Async handler invoked by alpaca-py inside its own loop."""
        try:
            ev = _normalize_bar(raw, self._asset_class)
            self._bus.publish_threadsafe(ev)
        except Exception as e:
            logger.warning("Stream handler error: {}", e)

    def _run_thread(self) -> None:
        backoff = 1.0
        max_backoff = 30.0
        while not self._stop_evt.is_set():
            try:
                logger.info(
                    "Alpaca {} stream connecting: {}",
                    self._asset_class.value, self._symbols,
                )
                self._stream = self._build_stream()
                self._stream.subscribe_bars(self._handle_bar, *self._symbols)
                self._started_at = datetime.now(timezone.utc)
                self._stream.run()  # blocking inside this thread until stop or error
                if self._stop_evt.is_set():
                    break
            except Exception as e:
                logger.warning("Alpaca stream errored ({}); reconnect in {:.1f}s", e, backoff)
            if self._stop_evt.is_set():
                break
            time.sleep(backoff)
            backoff = min(max_backoff, backoff * 2)
        logger.info("Alpaca {} stream thread exited", self._asset_class.value)

    def start(self) -> None:
        if self.running:
            return
        self._bus.bind_loop()  # capture caller's running loop
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run_thread, name=f"alpaca-stream-{self._asset_class.value}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if not self.running:
            return
        self._stop_evt.set()
        try:
            if self._stream is not None and hasattr(self._stream, "stop"):
                self._stream.stop()
        except Exception as e:
            logger.debug("stream.stop() raised: {}", e)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        self._stream = None

    def status(self) -> dict:
        return {
            "asset_class": self._asset_class.value,
            "symbols": list(self._symbols),
            "running": self.running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "subscriber_count": self._bus.subscriber_count,
            "dropped_total": self._bus.dropped_total,
        }
