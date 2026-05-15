"""Tests for ea.news — models, dedupe, ticker_tagger, poller (mocked)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from ea.eventbus import EventBus
from ea.news.dedupe import DedupeCache
from ea.news.models import NewsEvent, NewsItem, NewsSource
from ea.news.poller import NewsPoller
from ea.news.ticker_tagger import extract_candidate_tickers, tag_tickers


def test_news_item_id_is_stable():
    a = NewsItem.compute_id("Apple reports Q3", "https://x.com/a", datetime(2024, 1, 1, tzinfo=timezone.utc))
    b = NewsItem.compute_id("Apple Reports Q3", "https://x.com/a", datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert a == b  # case-insensitive normalization
    c = NewsItem.compute_id("Apple reports Q3", "https://x.com/different", datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert a != c


def test_dedupe_filter_new(tmp_path):
    cache = DedupeCache(tmp_path / "d.sqlite")
    new = cache.filter_new(["a", "b", "c"])
    assert set(new) == {"a", "b", "c"}
    new2 = cache.filter_new(["b", "c", "d"])
    assert set(new2) == {"d"}
    assert cache.size() == 4


def test_ticker_tagger_filters_false_positives():
    candidates = extract_candidate_tickers("CEO says AAPL will beat Q4 ETF expectations")
    assert "AAPL" in candidates
    assert "CEO" not in candidates
    assert "Q4" not in candidates
    assert "ETF" not in candidates


def test_ticker_tagger_validates_against_universe():
    text = "Apple (AAPL) and FAKE both rise"
    universe = {"AAPL", "MSFT"}
    tags = tag_tickers(text, universe)
    assert tags == ["AAPL"]


def test_ticker_tagger_parenthesized_wins():
    text = "Apple (AAPL) up"
    cands = extract_candidate_tickers(text)
    assert "AAPL" in cands


@pytest.mark.asyncio
async def test_poller_emits_news_event(tmp_path):
    bus = EventBus()
    bus.bind_loop()
    q = bus.subscribe()

    item = NewsItem(
        id=NewsItem.compute_id("test title", "https://example.com", datetime(2024, 1, 1, tzinfo=timezone.utc)),
        source=NewsSource.RSS, title="AAPL rises sharply", url="https://example.com",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc), raw_source_name="Test",
    )

    poller = NewsPoller(
        bus=bus, cache=DedupeCache(tmp_path / "d.sqlite"),
        watchlist_provider=lambda: [], known_universe={"AAPL"},
    )

    async def fake_rss(url, raw_source_name=None):
        return [item]

    with patch("ea.news.poller.fetch_rss", side_effect=fake_rss), \
         patch("ea.news.poller.DEFAULT_FEEDS", [("https://x", "Test")]):
        n = await poller.poll_rss_once()

    assert n == 1
    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert isinstance(event, NewsEvent)
    assert event.item.title == "AAPL rises sharply"
    assert "AAPL" in event.tickers


@pytest.mark.asyncio
async def test_poller_dedupes_across_calls(tmp_path):
    bus = EventBus()
    bus.bind_loop()
    item = NewsItem(
        id=NewsItem.compute_id("same title", "https://example.com", datetime(2024, 1, 1, tzinfo=timezone.utc)),
        source=NewsSource.RSS, title="same title", url="https://example.com",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    poller = NewsPoller(
        bus=bus, cache=DedupeCache(tmp_path / "d.sqlite"),
        watchlist_provider=lambda: [], known_universe=set(),
    )

    async def fake_rss(url, raw_source_name=None):
        return [item]

    with patch("ea.news.poller.fetch_rss", side_effect=fake_rss), \
         patch("ea.news.poller.DEFAULT_FEEDS", [("https://x", "Test")]):
        first = await poller.poll_rss_once()
        second = await poller.poll_rss_once()
    assert first == 1
    assert second == 0
