"""End-of-day report.

Writes a daily markdown summary to `reports/eod_<date>.md` so a 30-day paper
run can actually be reviewed after the fact (Phase A gate evidence). Pure
rendering + a thin async collector that pulls from the live subsystems.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ea.logging import logger


def render_eod_markdown(
    *,
    date: str,
    account: dict,
    positions: list[dict],
    orders_today: list[dict],
    signals_by_strategy: dict[str, int],
    risk: dict,
    alerts_today: list[dict],
) -> str:
    pnl = account.get("pnl_today", 0.0)
    pnl_pct = account.get("pnl_today_pct", 0.0)
    lines: list[str] = [
        f"# EOD report — {date}",
        "",
        "## Account",
        "",
        f"- Equity: ${account.get('equity', 0):,.2f}",
        f"- Cash: ${account.get('cash', 0):,.2f}",
        f"- P/L today: ${pnl:,.2f} ({pnl_pct:+.2f}%)",
        f"- PDT flag: {account.get('pattern_day_trader')} · "
        f"trading_blocked: {account.get('trading_blocked')}",
        "",
        "## Risk / breakers",
        "",
        f"- Daily loss: {risk.get('daily_loss_pct', 0)}% "
        f"(halted: {risk.get('daily_halted', False)})",
        f"- Weekly loss: {risk.get('weekly_loss_pct', 0)}% "
        f"(halted: {risk.get('weekly_halted', False)})",
        "",
        f"## Open positions ({len(positions)})",
        "",
    ]
    if positions:
        lines.append("| Symbol | Qty | Entry | Last | Unreal P/L | P/L % |")
        lines.append("|---|--:|--:|--:|--:|--:|")
        for p in positions:
            upl = p.get("unrealized_pl")
            uplp = p.get("unrealized_pl_pct")
            lines.append(
                f"| {p.get('symbol')} | {p.get('quantity')} | "
                f"{p.get('avg_entry_price')} | {p.get('current_price')} | "
                f"{('%.2f' % upl) if upl is not None else '—'} | "
                f"{('%.2f%%' % (uplp * 100)) if uplp is not None else '—'} |"
            )
    else:
        lines.append("_None._")

    lines += ["", f"## Orders today ({len(orders_today)})", ""]
    if orders_today:
        lines.append("| Time | Symbol | Side | Qty | Status | Error |")
        lines.append("|---|---|---|--:|---|---|")
        for o in orders_today:
            lines.append(
                f"| {o.get('submitted_at', '')} | {o.get('symbol')} | "
                f"{o.get('side')} | {o.get('quantity')} | {o.get('status')} | "
                f"{o.get('error') or ''} |"
            )
    else:
        lines.append("_No orders submitted today._")

    lines += ["", "## Signals by strategy", ""]
    if signals_by_strategy:
        for name, n in sorted(signals_by_strategy.items()):
            lines.append(f"- {name}: {n}")
    else:
        lines.append("_No signals today._")

    lines += ["", f"## Alerts today ({len(alerts_today)})", ""]
    if alerts_today:
        for a in alerts_today[:50]:
            lines.append(f"- `{a.get('level')}` {a.get('timestamp')} — {a.get('message')}")
    else:
        lines.append("_No alerts today._")

    return "\n".join(lines) + "\n"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def build_eod_report(
    *,
    order_mgr: Any | None,
    signal_consumer: Any | None,
    risk: Any | None,
) -> str:
    """Collect from the live subsystems and render markdown for today (UTC)."""
    from ea.monitoring import state as state_mod

    today = _today_str()
    try:
        account = await state_mod.account_snapshot()
    except Exception as e:
        account = {"error": str(e)}
    try:
        positions = await state_mod.positions_snapshot()
    except Exception:
        positions = []

    orders_today: list[dict] = []
    if order_mgr is not None:
        for r in order_mgr.recent:
            if r.submitted_at.strftime("%Y-%m-%d") != today:
                continue
            orders_today.append({
                "submitted_at": r.submitted_at.strftime("%H:%M:%S"),
                "symbol": r.request.symbol,
                "side": r.request.side.value,
                "quantity": float(r.request.quantity),
                "status": r.order.status.value if r.order else "failed",
                "error": r.error,
            })

    signals_by_strategy: dict[str, int] = defaultdict(int)
    if signal_consumer is not None:
        for o in signal_consumer.recent:
            if o.timestamp.strftime("%Y-%m-%d") == today:
                signals_by_strategy[o.signal.strategy] += 1

    alerts_today = [
        a for a in state_mod.alerts_snapshot(limit=100)
        if str(a.get("timestamp", "")).startswith(today)
    ]

    return render_eod_markdown(
        date=today,
        account=account,
        positions=positions,
        orders_today=orders_today,
        signals_by_strategy=dict(signals_by_strategy),
        risk=risk.snapshot() if risk is not None else {},
        alerts_today=alerts_today,
    )


async def write_eod_report(
    *,
    order_mgr: Any | None,
    signal_consumer: Any | None,
    risk: Any | None,
    outdir: str | Path = "reports",
) -> Path:
    md = await build_eod_report(
        order_mgr=order_mgr, signal_consumer=signal_consumer, risk=risk,
    )
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"eod_{_today_str()}.md"
    path.write_text(md, encoding="utf-8")
    logger.info("EOD report written: {}", path)
    return path
