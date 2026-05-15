"""Provider abstraction tests — both Gemini and Anthropic dispatched via mocks."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from ea.config import load_config
from ea.news.analyzer import (
    AnalysisCache, GeminiProvider, LLMResult, NewsAnalyzer,
)
from ea.news.models import NewsItem, NewsSource


def _item():
    return NewsItem(
        id="abc", source=NewsSource.RSS, title="AAPL beats", url="https://x",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def test_analyzer_disabled_without_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = load_config(profile="paper")
    object.__setattr__(cfg.env, "anthropic_api_key", None)
    object.__setattr__(cfg.env, "gemini_api_key", None)
    cache = AnalysisCache(tmp_path / "c.sqlite")
    a = NewsAnalyzer(cfg, cache=cache)
    assert a.enabled is False


@pytest.mark.asyncio
async def test_analyzer_uses_provider_and_caches(tmp_path):
    cfg = load_config(profile="paper")
    cache = AnalysisCache(tmp_path / "c.sqlite")

    fake_provider = type("P", (), {})()
    fake_provider.name = "gemini"
    fake_provider.enabled = True
    fake_provider.score = AsyncMock(return_value=LLMResult(
        data={"relevance": 0.8, "sentiment": "positive", "sentiment_score": 0.6,
              "materiality": 0.7, "rationale": "guidance raised"},
        tokens_in=120, tokens_out=40,
    ))

    a = NewsAnalyzer(cfg, cache=cache, provider=fake_provider)
    assert a.enabled

    result = await a.analyze(_item(), ["AAPL"])
    assert result is not None
    assert result.materiality == pytest.approx(0.7)
    assert fake_provider.score.called

    # Second call should hit cache, not provider
    fake_provider.score.reset_mock()
    cached = await a.analyze(_item(), ["AAPL"])
    assert cached is not None
    assert not fake_provider.score.called


@pytest.mark.asyncio
async def test_analyzer_promotes_to_deep_pass(tmp_path):
    cfg = load_config(profile="paper")
    cache = AnalysisCache(tmp_path / "c.sqlite")

    cheap = LLMResult(
        data={"relevance": 0.9, "sentiment": "positive", "sentiment_score": 0.7,
              "materiality": 0.85, "rationale": "FDA approval"},
        tokens_in=100, tokens_out=30,
    )
    deep = LLMResult(
        data={"catalyst_type": "fda", "direction_hint": 1, "confidence": 0.8,
              "rationale": "binary FDA approval positive for biotech"},
        tokens_in=200, tokens_out=50,
    )
    seq = [cheap, deep]

    fake = type("P", (), {})()
    fake.name = "gemini"
    fake.enabled = True
    async def fake_score(model, system, user, max_tokens):
        return seq.pop(0)
    fake.score = fake_score

    a = NewsAnalyzer(cfg, cache=cache, provider=fake)
    result = await a.analyze(_item(), ["BIO"])
    assert result is not None
    assert result.catalyst_type.value == "fda"
    assert result.direction_hint == 1
    assert result.confidence == 0.8
    assert "+" in (result.model_used or "")


def test_gemini_provider_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = load_config(profile="paper")
    object.__setattr__(cfg.env, "gemini_api_key", None)
    p = GeminiProvider(cfg)
    assert not p.enabled
