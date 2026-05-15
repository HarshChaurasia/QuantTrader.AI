"""News analyzer with pluggable LLM provider (Anthropic or Google Gemini).

Provider selection: config.profile.news.llm.provider = "anthropic" | "gemini" | "none".
Falls back to "none" (no-op) if the selected provider's API key is missing.

Two-tier scoring (same logic across providers):
1. Cheap pass — relevance, sentiment, materiality. Hits every item.
2. Deep pass — only on items above threshold; catalyst classification + direction.

Persists analyses in SQLite (per article id) so the same article is never re-scored.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol

from ea.config import REPO_ROOT, Config
from ea.logging import logger
from ea.news.models import CatalystType, NewsAnalysis, NewsItem, Sentiment

DEFAULT_CACHE_PATH = REPO_ROOT / "data" / "news_analysis.sqlite"

# Approx pricing per 1M tokens (May 2026 reference). Used for spend telemetry only.
# Gemini Flash free-tier is genuinely free up to 1500 req/day; cost shown for paid usage.
_PRICING = {
    "claude-haiku-4-5-20251001":  {"in": 1.00, "out": 5.00},
    "claude-sonnet-4-6":           {"in": 3.00, "out": 15.00},
    "gemini-2.5-flash-lite":       {"in": 0.0,  "out": 0.0},  # free tier
    "gemini-2.5-flash":            {"in": 0.0,  "out": 0.0},  # free tier
    "gemini-2.5-pro":              {"in": 1.25, "out": 5.00},
}


def _cost_usd(model: str, tin: int, tout: int) -> float:
    p = _PRICING.get(model, {"in": 0.0, "out": 0.0})
    return (tin / 1_000_000) * p["in"] + (tout / 1_000_000) * p["out"]


_CHEAP_SYS = (
    "You score market news. Output JSON only, no prose. "
    'Schema: {"relevance": 0..1, "sentiment": "positive"|"negative"|"neutral"|"mixed", '
    '"sentiment_score": -1..1, "materiality": 0..1, "rationale": "short string"}. '
    "relevance = market-relevant at all. materiality = likely to move price within days."
)

_DEEP_SYS = (
    "You classify market-news catalysts. Output JSON only. "
    'Schema: {"catalyst_type": "earnings"|"guidance"|"m_and_a"|"regulatory"|"fda"|"product"'
    '|"contract"|"legal"|"management"|"macro"|"other", "direction_hint": -1|0|1, '
    '"confidence": 0..1, "rationale": "short string"}.'
)


def _user_prompt(item: NewsItem, tickers: list[str]) -> str:
    return (
        f"TICKERS: {','.join(tickers) or '(none tagged)'}\n"
        f"SOURCE: {item.raw_source_name or item.source.value}\n"
        f"TITLE: {item.title}\n"
        f"SUMMARY: {(item.summary or '')[:600]}"
    )


def _extract_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    i = t.find("{")
    j = t.rfind("}")
    if i >= 0 and j > i:
        return t[i:j + 1]
    return t


# --- Providers ---

@dataclass
class LLMResult:
    data: dict
    tokens_in: int
    tokens_out: int


class LLMProvider(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...
    async def score(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult | None: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, config: Config):
        self._client = None
        if config.env.anthropic_api_key is not None:
            try:
                from anthropic import AsyncAnthropic
                self._client = AsyncAnthropic(api_key=config.env.anthropic_api_key.get_secret_value())
            except Exception as e:
                logger.warning("AnthropicProvider init failed: {}", e)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def score(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult | None:
        if not self.enabled:
            return None
        try:
            r = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:
            logger.warning("Anthropic call failed: {}", e)
            return None
        text = r.content[0].text if r.content else "{}"
        try:
            data = json.loads(_extract_json(text))
        except Exception as e:
            logger.warning("Anthropic JSON parse failed: {} -- raw: {}", e, text[:200])
            return None
        return LLMResult(
            data=data,
            tokens_in=getattr(r.usage, "input_tokens", 0) or 0,
            tokens_out=getattr(r.usage, "output_tokens", 0) or 0,
        )


class GeminiProvider:
    name = "gemini"

    def __init__(self, config: Config):
        self._client = None
        if config.env.gemini_api_key is not None:
            try:
                from google import genai  # noqa: F401 — import probe
                self._key = config.env.gemini_api_key.get_secret_value()
                # google-genai is sync; we'll run in a thread per call.
                from google.genai import Client
                self._client = Client(api_key=self._key)
            except Exception as e:
                logger.warning("GeminiProvider init failed: {}", e)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _call_sync(self, model: str, system: str, user: str, max_tokens: int):
        from google.genai import types as gtypes
        # Disable thinking mode — Gemini 2.5 Flash defaults to thinking which consumes
        # output tokens before producing JSON. We want fast structured output, not deliberation.
        kwargs = dict(
            system_instruction=system,
            response_mime_type="application/json",
            max_output_tokens=max_tokens,
            temperature=0.2,
        )
        try:
            kwargs["thinking_config"] = gtypes.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass  # older SDK without ThinkingConfig — ignore
        return self._client.models.generate_content(
            model=model,
            contents=user,
            config=gtypes.GenerateContentConfig(**kwargs),
        )

    async def score(self, model: str, system: str, user: str, max_tokens: int) -> LLMResult | None:
        if not self.enabled:
            return None
        try:
            r = await asyncio.to_thread(self._call_sync, model, system, user, max_tokens)
        except Exception as e:
            logger.warning("Gemini call failed: {}", e)
            return None
        text = (getattr(r, "text", "") or "").strip()
        if not text:
            return None
        try:
            data = json.loads(_extract_json(text))
        except Exception as e:
            logger.warning("Gemini JSON parse failed: {} -- raw: {}", e, text[:200])
            return None
        usage = getattr(r, "usage_metadata", None)
        tin = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        tout = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        return LLMResult(data=data, tokens_in=tin, tokens_out=tout)


def _build_provider(config: Config) -> LLMProvider | None:
    name = (config.profile.news.llm.provider or "none").lower()
    if name == "anthropic":
        return AnthropicProvider(config)
    if name == "gemini":
        return GeminiProvider(config)
    if name == "none":
        return None
    logger.warning("Unknown LLM provider {}; analysis disabled", name)
    return None


# --- Cache ---

class AnalysisCache:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_CACHE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS analysis (
                    article_id TEXT PRIMARY KEY,
                    payload    TEXT NOT NULL,
                    ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS spend (
                    day TEXT NOT NULL, model TEXT NOT NULL,
                    tin INTEGER, tout INTEGER, cost REAL,
                    PRIMARY KEY (day, model)
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path)
        try:
            yield c; c.commit()
        finally:
            c.close()

    def get(self, article_id: str) -> NewsAnalysis | None:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT payload FROM analysis WHERE article_id = ?", (article_id,)).fetchone()
        if not row:
            return None
        try:
            return NewsAnalysis.model_validate_json(row[0])
        except Exception:
            return None

    def put(self, article_id: str, analysis: NewsAnalysis) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO analysis(article_id, payload) VALUES (?, ?)",
                      (article_id, analysis.model_dump_json()))

    def add_spend(self, model: str, tin: int, tout: int) -> float:
        day = datetime.now(timezone.utc).date().isoformat()
        cost = _cost_usd(model, tin, tout)
        with self._lock, self._connect() as c:
            c.execute("""
                INSERT INTO spend(day, model, tin, tout, cost) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(day, model) DO UPDATE SET
                    tin = tin + excluded.tin, tout = tout + excluded.tout, cost = cost + excluded.cost
            """, (day, model, tin, tout, cost))
        return cost

    def today_spend(self) -> float:
        day = datetime.now(timezone.utc).date().isoformat()
        with self._lock, self._connect() as c:
            row = c.execute("SELECT COALESCE(SUM(cost), 0) FROM spend WHERE day = ?", (day,)).fetchone()
        return float(row[0]) if row else 0.0


# --- Analyzer ---

class NewsAnalyzer:
    def __init__(self, config: Config, cache: AnalysisCache | None = None,
                 provider: LLMProvider | None = None):
        self._config = config
        self._cache = cache or AnalysisCache()
        self._provider = provider if provider is not None else _build_provider(config)

    @property
    def enabled(self) -> bool:
        return self._provider is not None and self._provider.enabled

    @property
    def provider_name(self) -> str:
        return self._provider.name if self._provider else "none"

    @property
    def cache(self) -> AnalysisCache:
        return self._cache

    async def analyze(self, item: NewsItem, tickers: list[str]) -> NewsAnalysis | None:
        if not self.enabled:
            return None

        cached = self._cache.get(item.id)
        if cached is not None:
            return cached

        llm_cfg = self._config.profile.news.llm
        if self._cache.today_spend() >= llm_cfg.daily_spend_alert_usd * 5:
            logger.warning("LLM daily spend hard cap reached")
            return None

        cheap = await self._cheap_score(item, tickers, llm_cfg.cheap_model, max_tokens=800)
        if cheap is None:
            return None

        if (cheap.materiality >= llm_cfg.materiality_threshold
                and cheap.relevance >= llm_cfg.relevance_threshold):
            deep = await self._deep_classify(item, tickers, llm_cfg.deep_model, cheap, max_tokens=800)
            if deep is not None:
                merged = cheap.model_copy(update={
                    "catalyst_type": deep.catalyst_type,
                    "direction_hint": deep.direction_hint,
                    "confidence": deep.confidence,
                    "rationale": deep.rationale or cheap.rationale,
                    "model_used": f"{llm_cfg.cheap_model}+{llm_cfg.deep_model}",
                    "cost_tokens_in": cheap.cost_tokens_in + deep.cost_tokens_in,
                    "cost_tokens_out": cheap.cost_tokens_out + deep.cost_tokens_out,
                })
                self._cache.put(item.id, merged)
                return merged

        self._cache.put(item.id, cheap)
        return cheap

    async def _cheap_score(self, item: NewsItem, tickers: list[str], model: str,
                           max_tokens: int = 800) -> NewsAnalysis | None:
        result = await self._provider.score(model, _CHEAP_SYS, _user_prompt(item, tickers), max_tokens=max_tokens)
        if result is None:
            return None
        self._cache.add_spend(model, result.tokens_in, result.tokens_out)
        try:
            return NewsAnalysis(
                relevance=float(result.data.get("relevance", 0.0)),
                sentiment=Sentiment(result.data.get("sentiment", "neutral")),
                sentiment_score=float(result.data.get("sentiment_score", 0.0)),
                materiality=float(result.data.get("materiality", 0.0)),
                rationale=result.data.get("rationale"),
                model_used=model,
                cost_tokens_in=result.tokens_in,
                cost_tokens_out=result.tokens_out,
            )
        except Exception as e:
            logger.warning("cheap-pass coerce failed: {}", e)
            return None

    async def _deep_classify(self, item: NewsItem, tickers: list[str], model: str,
                             prior: NewsAnalysis, max_tokens: int = 800) -> NewsAnalysis | None:
        user = _user_prompt(item, tickers) + (
            f"\nPRIOR: sentiment={prior.sentiment.value}, materiality={prior.materiality:.2f}"
        )
        result = await self._provider.score(model, _DEEP_SYS, user, max_tokens=max_tokens)
        if result is None:
            return None
        self._cache.add_spend(model, result.tokens_in, result.tokens_out)
        try:
            return NewsAnalysis(
                relevance=prior.relevance,
                sentiment=prior.sentiment,
                sentiment_score=prior.sentiment_score,
                materiality=prior.materiality,
                catalyst_type=CatalystType(result.data.get("catalyst_type", "other")),
                direction_hint=int(result.data.get("direction_hint", 0)),
                confidence=float(result.data.get("confidence", 0.5)),
                rationale=result.data.get("rationale"),
                model_used=model,
                cost_tokens_in=result.tokens_in,
                cost_tokens_out=result.tokens_out,
            )
        except Exception as e:
            logger.warning("deep-pass coerce failed: {}", e)
            return None
