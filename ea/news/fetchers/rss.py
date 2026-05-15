"""Generic RSS reader. Used for Reuters, MarketWatch, CoinDesk, central banks, etc."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import feedparser

from ea.news.models import NewsItem, NewsSource


def _parse_dt(parsed) -> datetime:
    if parsed is None:
        return datetime.now(timezone.utc)
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def _fetch_sync(url: str, raw_source_name: str | None) -> list[NewsItem]:
    feed = feedparser.parse(url)
    items: list[NewsItem] = []
    for e in feed.entries:
        title = (getattr(e, "title", "") or "").strip()
        link = (getattr(e, "link", "") or "").strip()
        if not title or not link:
            continue
        published = _parse_dt(getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None))
        summary = (getattr(e, "summary", "") or "").strip() or None
        item_id = NewsItem.compute_id(title, link, published)
        items.append(NewsItem(
            id=item_id, source=NewsSource.RSS, source_id=getattr(e, "id", None),
            title=title, url=link, published_at=published, summary=summary,
            raw_source_name=raw_source_name,
        ))
    return items


async def fetch_rss(url: str, raw_source_name: str | None = None) -> list[NewsItem]:
    return await asyncio.to_thread(_fetch_sync, url, raw_source_name)


# Curated free RSS feeds. Polled by the NewsPoller.
DEFAULT_FEEDS: list[tuple[str, str]] = [
    # General market
    ("https://feeds.marketwatch.com/marketwatch/topstories/", "MarketWatch"),
    ("https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best", "Reuters"),
    # Crypto
    ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
    # SEC press
    ("https://www.sec.gov/news/pressreleases.rss", "SEC Press"),
]
