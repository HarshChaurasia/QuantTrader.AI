"""One-shot Gemini smoke test. Run: uv run python scripts/smoke_gemini.py"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ea.config import load_config
from ea.news.analyzer import NewsAnalyzer
from ea.news.models import NewsItem, NewsSource


async def main():
    cfg = load_config(profile="paper")
    a = NewsAnalyzer(cfg)
    print(f"provider={a.provider_name} enabled={a.enabled}")
    if not a.enabled:
        print("Set GEMINI_API_KEY in .env first.")
        return

    item = NewsItem(
        id="smoke-test",
        source=NewsSource.RSS,
        title="Apple beats Q4 earnings, raises guidance for next quarter",
        url="https://example.com/aapl",
        published_at=datetime.now(timezone.utc),
        summary="AAPL reports EPS of $2.15 vs $1.98 expected, revenue +9% YoY, guides Q1 above consensus",
        raw_source_name="Test",
    )
    print("Calling Gemini...")
    result = await a.analyze(item, ["AAPL"])
    if result is None:
        print("No result — check logs for error")
        return
    print(f"\n--- RESULT ---")
    print(f"relevance:       {result.relevance:.2f}")
    print(f"sentiment:       {result.sentiment.value} (score={result.sentiment_score:+.2f})")
    print(f"materiality:     {result.materiality:.2f}")
    print(f"catalyst_type:   {result.catalyst_type.value}")
    print(f"direction_hint:  {result.direction_hint:+d}")
    print(f"confidence:      {result.confidence:.2f}")
    print(f"rationale:       {result.rationale}")
    print(f"model_used:      {result.model_used}")
    print(f"tokens:          in={result.cost_tokens_in} out={result.cost_tokens_out}")
    print(f"today_spend_usd: {a.cache.today_spend():.6f}")


if __name__ == "__main__":
    asyncio.run(main())
