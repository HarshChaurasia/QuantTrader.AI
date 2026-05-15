"""SEC EDGAR full-text filings feed.

Polls EDGAR's RSS for recent 8-K / 10-Q / 10-K filings. Each entry includes the
issuer ticker (when known) in the title — we extract it.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import feedparser

from ea.news.models import NewsItem, NewsSource

# Filter by form types — 8-K is the primary catalyst feed
EDGAR_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&type={form}&dateb=&owner=include&count=40&output=atom"
)

# Title looks like "8-K - SOMECORP INC (0001234567) (Filer)"
_TICKER_PAREN_RE = re.compile(r"\(([A-Z]{1,5})\)")


def _ticker_from_title(title: str) -> str | None:
    """SEC EDGAR titles include CIK not ticker. We map best-effort via paren extraction."""
    for m in _TICKER_PAREN_RE.finditer(title):
        tok = m.group(1)
        # CIKs are numeric — paren tokens with letters are likely tickers
        if tok.isalpha():
            return tok
    return None


def _parse_dt(parsed) -> datetime:
    if parsed is None:
        return datetime.now(timezone.utc)
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def _fetch_sync(form: str = "8-K") -> list[NewsItem]:
    url = EDGAR_URL.format(form=form)
    feed = feedparser.parse(
        url, request_headers={"User-Agent": "ea-trading-research contact@example.local"}
    )
    items: list[NewsItem] = []
    for e in feed.entries:
        title = (getattr(e, "title", "") or "").strip()
        link = (getattr(e, "link", "") or "").strip()
        if not title or not link:
            continue
        published = _parse_dt(getattr(e, "updated_parsed", None) or getattr(e, "published_parsed", None))
        summary = (getattr(e, "summary", "") or "").strip() or None
        item_id = NewsItem.compute_id(title, link, published)
        items.append(NewsItem(
            id=item_id, source=NewsSource.SEC_EDGAR, source_id=getattr(e, "id", None),
            title=title, url=link, published_at=published, summary=summary,
            raw_source_name=f"SEC {form}",
        ))
    return items


async def fetch_edgar(form: str = "8-K") -> list[NewsItem]:
    return await asyncio.to_thread(_fetch_sync, form)


def extract_ticker(item: NewsItem) -> str | None:
    return _ticker_from_title(item.title)
