"""yfinance per-ticker news. Already ticker-tagged by the library."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ea.news.models import NewsItem, NewsSource


def _fetch_sync(symbol: str) -> list[tuple[NewsItem, str]]:
    """Returns list of (item, ticker) — ticker is the symbol we requested."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(symbol)
        raw = ticker.news or []
    except Exception:
        return []

    out: list[tuple[NewsItem, str]] = []
    for r in raw:
        # yfinance schema varies between versions — defend on both shapes
        content = r.get("content") if isinstance(r, dict) else None
        title = (content or r).get("title") if isinstance((content or r), dict) else None
        link = None
        click = (content or r).get("clickThroughUrl") if isinstance((content or r), dict) else None
        if isinstance(click, dict):
            link = click.get("url")
        if not link:
            canon = (content or r).get("canonicalUrl") if isinstance((content or r), dict) else None
            if isinstance(canon, dict):
                link = canon.get("url")
        if not link:
            link = (content or r).get("link") if isinstance((content or r), dict) else None
        if not title or not link:
            continue

        # publish date
        pub_iso = (content or r).get("pubDate") if isinstance((content or r), dict) else None
        if isinstance(pub_iso, str):
            try:
                published = datetime.fromisoformat(pub_iso.replace("Z", "+00:00"))
            except Exception:
                published = datetime.now(timezone.utc)
        elif "providerPublishTime" in r:
            published = datetime.fromtimestamp(r["providerPublishTime"], tz=timezone.utc)
        else:
            published = datetime.now(timezone.utc)

        summary = (content or r).get("summary") if isinstance((content or r), dict) else None
        publisher = (content or r).get("provider", {}) if isinstance((content or r), dict) else {}
        publisher_name = publisher.get("displayName") if isinstance(publisher, dict) else None

        item_id = NewsItem.compute_id(title, link, published)
        out.append((
            NewsItem(
                id=item_id, source=NewsSource.YAHOO,
                title=title, url=link, published_at=published, summary=summary,
                raw_source_name=publisher_name or "Yahoo",
            ),
            symbol,
        ))
    return out


async def fetch_yahoo_for_symbol(symbol: str) -> list[tuple[NewsItem, str]]:
    return await asyncio.to_thread(_fetch_sync, symbol)
