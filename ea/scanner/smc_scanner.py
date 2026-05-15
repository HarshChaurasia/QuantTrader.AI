"""SMC scanner — periodic scan across stocks, crypto, forex.

Two modes:
- "curated": scan explicit lists set via set_universes()
- "all": scan every symbol present in the BarStore across all asset classes

Cached for the dashboard scanner panel.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ea.brokers.models import AssetClass
from ea.data.store import BarStore
from ea.logging import logger
from ea.strategies.smc.strategy import evaluate_setup


@dataclass
class ScanResult:
    symbol: str
    asset_class: AssetClass
    setup: dict | None
    bars_in_store: int
    error: str | None = None
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def has_setup(self) -> bool:
        return self.setup is not None


class SMCScanner:
    def __init__(
        self,
        store: BarStore,
        *,
        scan_interval_s: float = 300.0,
        risk_reward: float = 2.0,
        max_concurrency: int = 16,
        timeframe: str = "1Hour",
    ):
        self._store = store
        self._interval = scan_interval_s
        self._rr = risk_reward
        self._max_conc = max_concurrency
        self._timeframe = timeframe
        self._stock_symbols: list[str] = []
        self._crypto_symbols: list[str] = []
        self._forex_symbols: list[str] = []
        self._mode: str = "curated"
        self._results: dict[str, ScanResult] = {}
        self._task: asyncio.Task | None = None
        self._stop_evt = asyncio.Event()
        self._scans_completed = 0
        self._last_scan_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def results(self) -> list[ScanResult]:
        return list(self._results.values())

    @property
    def scans_completed(self) -> int:
        return self._scans_completed

    @property
    def last_scan_at(self) -> datetime | None:
        return self._last_scan_at

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in ("curated", "all"):
            raise ValueError("mode must be 'curated' or 'all'")
        self._mode = mode

    def set_universes(
        self,
        stocks: list[str] | None = None,
        crypto: list[str] | None = None,
        forex: list[str] | None = None,
    ) -> None:
        if stocks is not None:
            self._stock_symbols = [s.upper().strip() for s in stocks if s.strip()]
        if crypto is not None:
            self._crypto_symbols = [s.upper().strip() for s in crypto if s.strip()]
        if forex is not None:
            self._forex_symbols = [s.upper().strip() for s in forex if s.strip()]

    def _resolve_targets(self) -> list[tuple[str, AssetClass]]:
        if self._mode == "all":
            stocks = self._store.list_symbols(AssetClass.STOCK, self._timeframe)
            crypto = self._store.list_symbols(AssetClass.CRYPTO, self._timeframe)
            forex = self._store.list_symbols(AssetClass.FOREX, self._timeframe)
        else:
            stocks = self._stock_symbols
            crypto = self._crypto_symbols
            forex = self._forex_symbols
        out: list[tuple[str, AssetClass]] = []
        out += [(s, AssetClass.STOCK) for s in stocks]
        out += [(s, AssetClass.CRYPTO) for s in crypto]
        out += [(s, AssetClass.FOREX) for s in forex]
        return out

    def _scan_one(self, symbol: str, asset_class: AssetClass) -> ScanResult:
        try:
            df = self._store.get_bars(symbol, self._timeframe, asset_class)
        except Exception as e:
            return ScanResult(symbol=symbol, asset_class=asset_class, setup=None,
                              bars_in_store=0, error=str(e))
        if df is None or df.empty:
            return ScanResult(symbol=symbol, asset_class=asset_class, setup=None,
                              bars_in_store=0, error="no bars in store")
        try:
            setup = evaluate_setup(df, risk_reward=self._rr)
        except Exception as e:
            return ScanResult(symbol=symbol, asset_class=asset_class, setup=None,
                              bars_in_store=int(len(df)), error=str(e))
        return ScanResult(symbol=symbol, asset_class=asset_class, setup=setup,
                          bars_in_store=int(len(df)))

    async def scan_once(self) -> int:
        targets = self._resolve_targets()
        if not targets:
            self._scans_completed += 1
            self._last_scan_at = datetime.now(timezone.utc)
            return 0

        sem = asyncio.Semaphore(self._max_conc)

        async def _one(symbol: str, ac: AssetClass) -> ScanResult:
            async with sem:
                return await asyncio.to_thread(self._scan_one, symbol, ac)

        results = await asyncio.gather(*[_one(s, ac) for s, ac in targets])
        # Replace cache (drop symbols no longer in universe)
        self._results = {r.symbol: r for r in results}
        self._scans_completed += 1
        self._last_scan_at = datetime.now(timezone.utc)
        with_setups = sum(1 for r in results if r.has_setup)
        in_zone = sum(1 for r in results if r.has_setup and r.setup["status"] == "in_zone")
        logger.info("SMC scan ({}): {} scanned, {} setups, {} in zone",
                    self._mode, len(results), with_setups, in_zone)
        return len(results)

    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self.scan_once()
            except Exception as e:
                logger.warning("scanner loop error: {}", e)
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self.running:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop(), name="smc-scanner")

    async def stop(self) -> None:
        self._stop_evt.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    def status(self) -> dict:
        with_setups = sum(1 for r in self._results.values() if r.has_setup)
        in_zone = sum(1 for r in self._results.values()
                      if r.has_setup and r.setup["status"] == "in_zone")
        approaching = sum(1 for r in self._results.values()
                          if r.has_setup and r.setup["status"] == "approaching")
        watching = sum(1 for r in self._results.values()
                       if r.has_setup and r.setup["status"] == "watching")
        return {
            "running": self.running,
            "mode": self._mode,
            "timeframe": self._timeframe,
            "scans_completed": self._scans_completed,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
            "interval_s": self._interval,
            "universe_size": len(self._resolve_targets()),
            "results_cached": len(self._results),
            "setups_found": with_setups,
            "in_zone": in_zone,
            "approaching": approaching,
            "watching": watching,
        }
