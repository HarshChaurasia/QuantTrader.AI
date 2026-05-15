"""Map news items to tickers.

Two strategies:
1. Symbol-aware fetchers (yfinance per-ticker) already know their ticker — just trust.
2. Generic-feed items (SEC EDGAR, Reuters RSS) get a heuristic pass:
   - Regex for uppercase 1-5 letter tokens in title (e.g. "Apple (AAPL) reports...")
   - Validate against a known universe list to reject false positives like "CEO" or "ETF"

A future enhancement is a CIK→ticker map from sec.gov/cgi-bin/browse-edgar?action=getcompany,
but for v1 the heuristic + known-universe filter is good enough.
"""
from __future__ import annotations

import re

# Tokens that look like tickers but never are. Extend as needed.
_FALSE_POSITIVE_TOKENS: set[str] = {
    "I", "A", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS", "IT",
    "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    "ALL", "AND", "ANY", "ARE", "BUT", "CAN", "FOR", "GET", "HAS", "HAD", "HE",
    "HER", "HIS", "HOW", "MAY", "NEW", "NOT", "NOW", "OFF", "ONE", "OUR", "OUT",
    "PER", "SEE", "SHE", "TOP", "TWO", "USE", "WHO", "WHY", "YES", "YET", "YOU",
    "WILL", "FROM", "INTO", "OVER", "WITH", "THIS", "THAT", "WHEN", "THEY",
    # Common business shorthand that looks like a ticker
    "CEO", "CFO", "COO", "CTO", "USA", "EU", "UK", "GDP", "CPI", "PPI",
    "ETF", "IPO", "API", "SEC", "FDA", "FED", "IRS", "FBI", "DOJ", "FTC",
    "Q1", "Q2", "Q3", "Q4", "YTD", "YOY", "EPS", "EBITDA", "PE", "PEG",
    "AI", "ML", "VR", "AR", "EV", "ESG", "CRM",
}

_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
_PARENTHESES_TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)")  # e.g. "Apple (AAPL)"


def extract_candidate_tickers(text: str) -> set[str]:
    """Pull uppercase short tokens from text; not yet validated against a universe."""
    if not text:
        return set()
    candidates: set[str] = set()
    # High-confidence: anything in parentheses
    for m in _PARENTHESES_TICKER_RE.finditer(text):
        candidates.add(m.group(1))
    # Lower-confidence: bare uppercase tokens
    for m in _TICKER_RE.finditer(text):
        tok = m.group(1)
        if tok in _FALSE_POSITIVE_TOKENS:
            continue
        if len(tok) < 2:
            continue
        candidates.add(tok)
    return candidates


def tag_tickers(text: str, known_universe: set[str] | None = None) -> list[str]:
    """Return tickers found in `text` that exist in `known_universe`.

    If `known_universe` is None, returns all candidate tokens (caller filters).
    """
    candidates = extract_candidate_tickers(text)
    if known_universe is None:
        return sorted(candidates)
    return sorted(candidates & known_universe)
