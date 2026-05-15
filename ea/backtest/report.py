"""Persist a backtest (or walk-forward) result to disk as markdown + JSON.

Markdown is for a human to review a run later; JSON is for diffing runs or
comparing paper P&L against backtest expectation (the Phase A gate).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ea.logging import logger


def _result_payload(result: Any) -> dict:
    # Walk-forward result
    if hasattr(result, "windows"):
        return {
            "kind": "walk_forward",
            "starting_equity": result.starting_equity,
            "ending_equity": result.ending_equity,
            "sharpe": result.sharpe,
            "max_drawdown_pct": result.max_drawdown_pct,
            "cagr": result.cagr,
            "n_trades": result.n_trades,
            "win_rate": result.win_rate,
            "windows": [
                {
                    "start": ws.isoformat(),
                    "end": we.isoformat(),
                    "return_pct": (r.ending_equity / r.starting_equity - 1) * 100,
                    "sharpe": r.sharpe,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "n_trades": r.n_trades,
                }
                for ws, we, r in result.windows
            ],
            "equity_curve": {
                ts.isoformat(): float(v)
                for ts, v in result.combined_equity.items()
            },
        }
    # Single backtest result
    return {
        "kind": "backtest",
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "sharpe": result.sharpe,
        "max_drawdown_pct": result.max_drawdown_pct,
        "cagr": result.cagr,
        "n_trades": result.n_trades,
        "win_rate": result.win_rate,
        "avg_pnl_pct": result.avg_pnl_pct,
        "by_strategy": result.by_strategy,
        "config": result.config_summary,
        "trades": [
            {
                "symbol": t.symbol,
                "strategy": t.strategy,
                "entry_date": t.entry_date.isoformat() if t.entry_date else None,
                "entry_price": t.entry_price,
                "exit_date": t.exit_date.isoformat() if t.exit_date else None,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
            }
            for t in result.trades
        ],
        "equity_curve": {
            ts.isoformat(): float(v) for ts, v in result.equity_curve.items()
        },
    }


def write_report(result: Any, outdir: str | Path = "reports", label: str = "backtest") -> Path:
    """Write `<outdir>/<label>_<UTC timestamp>.{md,json}`; return the .md path."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = out / f"{label}_{stamp}"

    payload = _result_payload(result)
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    md = [f"# Backtest report — {stamp} UTC", "", "```"]
    md += result.summary_lines()
    md.append("```")
    if payload["kind"] == "backtest" and payload["by_strategy"]:
        md += ["", "## By strategy", ""]
        for name, st in payload["by_strategy"].items():
            wr = (st["wins"] / st["trades"] * 100) if st["trades"] else 0
            md.append(f"- **{name}**: {st['trades']} trades, ${st['pnl']:+,.2f} pnl, {wr:.1f}% wins")
    md_path = base.with_suffix(".md")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    logger.info("backtest report written: {} (+ .json)", md_path)
    return md_path
