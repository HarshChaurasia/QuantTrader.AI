"""NewsPoller — schedules fetchers, dedupes, tags tickers, publishes NewsEvents.

Runs as a long-lived asyncio task. Pulls all sources on an interval, filters new
items via the dedupe cache, tags tickers, publishes to the event bus.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from ea.brokers.models import AssetClass
from ea.eventbus import EventBus, get_bus
from ea.logging import logger
from ea.news.analyzer import NewsAnalyzer
from ea.news.dedupe import DedupeCache
from ea.news.fetchers.rss import DEFAULT_FEEDS, fetch_rss
from ea.news.fetchers.sec_edgar import extract_ticker as edgar_ticker
from ea.news.fetchers.sec_edgar import fetch_edgar
from ea.news.fetchers.yahoo import fetch_yahoo_for_symbol
from ea.news.models import NewsEvent, NewsItem, NewsSource
from ea.news.ticker_tagger import tag_tickers


class NewsPoller:
    def __init__(
        self,
        bus: EventBus | None = None,
        cache: DedupeCache | None = None,
        watchlist_provider: Callable[[], list[str]] | None = None,
        known_universe: set[str] | None = None,
        analyzer: NewsAnalyzer | None = None,
        rss_interval_s: float = 300.0,
        edgar_interval_s: float = 120.0,
        yahoo_interval_s: float = 300.0,
    ):
        self._bus = bus or get_bus()
        self._cache = cache or DedupeCache()
        self._wl = watchlist_provider or (lambda: [])
        self._universe = known_universe or set()
        self._analyzer = analyzer
        self._rss_i = rss_interval_s
        self._edgar_i = edgar_interval_s
        self._yahoo_i = yahoo_interval_s
        self._task: asyncio.Task | None = None
        self._stop_evt = asyncio.Event()
        self._total_published = 0
        self._total_analyzed = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def total_published(self) -> int:
        return self._total_published

    async def _emit(self, item: NewsItem, tickers: list[str]) -> None:
        if self._cache.seen(item.id):
            return
        self._cache.mark(item.id)
        asset_classes = [AssetClass.STOCK] if tickers else []
        analysis = None
        if self._analyzer is not None and self._analyzer.enabled:
            try:
                analysis = await self._analyzer.analyze(item, tickers)
                if analysis is not None:
                    self._total_analyzed += 1
            except Exception as e:
                logger.warning("analyzer failed for {}: {}", item.id, e)
        ev = NewsEvent(item=item, tickers=tickers, asset_classes=asset_classes, analysis=analysis)
        await self._bus.publish(ev)
        self._total_published += 1

    async def poll_edgar_once(self) -> int:
        items = await fetch_edgar("8-K")
        n = 0
        for it in items:
            t = edgar_ticker(it)
            tickers = [t] if t and (not self._universe or t in self._universe) else []
            before = self._total_published
            await self._emit(it, tickers)
            if self._total_published > before:
                n += 1
        logger.debug("EDGAR poll: {} new items", n)
        return n

    async def poll_rss_once(self) -> int:
        n = 0
        for url, name in DEFAULT_FEEDS:
            try:
                items = await fetch_rss(url, raw_source_name=name)
            except Exception as e:
                logger.warning("rss feed {} failed: {}", name, e)
                continue
            for it in items:
                tickers = tag_tickers(it.title + " " + (it.summary or ""), self._universe or None)
                before = self._total_published
                await self._emit(it, tickers)
                if self._total_published > before:
                    n += 1
        logger.debug("RSS poll: {} new items", n)
        return n

    async def poll_yahoo_once(self) -> int:
        n = 0
        symbols = [s for s in self._wl() if "/" not in s]  # skip crypto pairs
        for sym in symbols:
            try:
                pairs = await fetch_yahoo_for_symbol(sym)
            except Exception as e:
                logger.warning("yahoo {} failed: {}", sym, e)
                continue
            for it, tk in pairs:
                before = self._total_published
                await self._emit(it, [tk])
                if self._total_published > before:
                    n += 1
        logger.debug("Yahoo poll ({}): {} new items", len(symbols), n)
        return n

    async def _loop(self, fn, interval: float, name: str) -> None:
        while not self._stop_evt.is_set():
            try:
                await fn()
            except Exception as e:
                logger.warning("{} loop error: {}", name, e)
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> None:
        logger.info("NewsPoller starting")
        self._stop_evt.clear()
        try:
            await asyncio.gather(
                self._loop(self.poll_edgar_once, self._edgar_i, "edgar"),
                self._loop(self.poll_rss_once, self._rss_i, "rss"),
                self._loop(self.poll_yahoo_once, self._yahoo_i, "yahoo"),
            )
        finally:
            logger.info("NewsPoller stopped; total published={}", self._total_published)

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self.run(), name="news-poller")

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
        s = {
            "running": self.running,
            "total_published": self._total_published,
            "total_analyzed": self._total_analyzed,
            "cache_size": self._cache.size(),
            "analyzer_enabled": self._analyzer.enabled if self._analyzer else False,
        }
        if self._analyzer is not None and self._analyzer.enabled:
            s["llm_spend_today_usd"] = round(self._analyzer.cache.today_spend(), 4)
        return s
