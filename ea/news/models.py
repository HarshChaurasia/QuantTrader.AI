"""News pipeline pydantic models.

Three layers:
- NewsItem: raw article as fetched from a source. No interpretation.
- NewsAnalysis: LLM-derived structured score (Phase A.5 fills this in). Until then,
  NewsEvent.analysis stays None.
- NewsEvent: what hits the event bus. NewsItem + tickers + optional analysis +
  fetch metadata.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from ea.brokers.models import AssetClass


class NewsSource(str, Enum):
    SEC_EDGAR = "sec_edgar"
    YAHOO = "yahoo"
    RSS = "rss"
    REDDIT = "reddit"
    OTHER = "other"


class CatalystType(str, Enum):
    """LLM-classified material catalyst — Phase A.5."""
    EARNINGS = "earnings"
    GUIDANCE = "guidance"
    M_AND_A = "m_and_a"
    REGULATORY = "regulatory"
    FDA = "fda"
    PRODUCT = "product"
    CONTRACT = "contract"
    LEGAL = "legal"
    MANAGEMENT = "management"
    MACRO = "macro"
    OTHER = "other"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class NewsItem(BaseModel):
    """Raw news article. Source-agnostic shape."""
    model_config = ConfigDict(frozen=True)

    id: str                      # stable content hash — same article from two sources collapses
    source: NewsSource
    source_id: str | None = None  # native ID from the source if available
    title: str
    url: str
    published_at: datetime
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str | None = None   # short snippet if available
    content: str | None = None   # full text if available (rare in RSS)
    language: str = "en"
    raw_source_name: str | None = None  # e.g. "Reuters" inside an RSS feed

    @staticmethod
    def compute_id(title: str, url: str, published_at: datetime | None = None) -> str:
        """Stable content hash. Same content from two feeds → same id (dedup)."""
        norm = (title.strip().lower() + "|" + url.strip().lower()).encode("utf-8")
        if published_at is not None:
            norm += b"|" + published_at.date().isoformat().encode("utf-8")
        return hashlib.sha256(norm).hexdigest()[:24]


class NewsAnalysis(BaseModel):
    """LLM-derived analysis. Phase A.5 generates these."""
    model_config = ConfigDict(frozen=True)

    relevance: float                   # 0..1 — is this market-relevant at all
    sentiment: Sentiment
    sentiment_score: float             # -1..+1
    materiality: float                 # 0..1 — likelihood of moving price
    catalyst_type: CatalystType = CatalystType.OTHER
    direction_hint: int = 0            # -1, 0, +1 (long/short tilt suggestion)
    confidence: float = 0.5            # LLM self-reported confidence
    rationale: str | None = None       # one-line LLM explanation
    model_used: str | None = None      # e.g. "claude-haiku-4-5-20251001"
    cost_tokens_in: int = 0
    cost_tokens_out: int = 0
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NewsEvent(BaseModel):
    """What hits the event bus."""
    model_config = ConfigDict(frozen=True)

    item: NewsItem
    tickers: list[str] = Field(default_factory=list)         # ["AAPL", "MSFT"] — empty if untagged
    asset_classes: list[AssetClass] = Field(default_factory=list)
    analysis: NewsAnalysis | None = None                     # populated by Phase A.5 LLM pass

    @property
    def has_analysis(self) -> bool:
        return self.analysis is not None
