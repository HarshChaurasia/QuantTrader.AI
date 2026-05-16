"""EOD report rendering + operational alert evaluation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ea.monitoring.alerts import evaluate_alerts
from ea.monitoring.reports import render_eod_markdown


def test_render_eod_markdown_smoke():
    md = render_eod_markdown(
        date="2026-05-15",
        account={"equity": 10500.0, "cash": 4000.0, "pnl_today": 500.0,
                 "pnl_today_pct": 5.0, "pattern_day_trader": False,
                 "trading_blocked": False},
        positions=[{"symbol": "BTC/USD", "quantity": 0.1, "avg_entry_price": 60000,
                    "current_price": 61000, "unrealized_pl": 100.0,
                    "unrealized_pl_pct": 0.0166}],
        orders_today=[{"submitted_at": "14:30:00", "symbol": "BTC/USD",
                       "side": "buy", "quantity": 0.1, "status": "filled",
                       "error": None}],
        signals_by_strategy={"smc_scalp": 3, "smc": 1},
        risk={"daily_loss_pct": 0.0, "weekly_loss_pct": 0.0,
              "daily_halted": False, "weekly_halted": False},
        alerts_today=[{"level": "info", "timestamp": "2026-05-15T14:00:00",
                       "message": "Stack started"}],
    )
    assert "# EOD report — 2026-05-15" in md
    assert "P/L today: $500.00 (+5.00%)" in md
    assert "smc_scalp: 3" in md
    assert "BTC/USD" in md


class _Risk:
    def snapshot(self):
        return {"daily_halted": True, "daily_loss_pct": 3.1,
                "weekly_halted": False, "weekly_loss_pct": 0.0}


class _Stream:
    running = False


class _Order:
    def __init__(self):
        from types import SimpleNamespace
        self.request = SimpleNamespace(symbol="ETH/USD", client_order_id="c1")
        self.error = None
        self.order = SimpleNamespace(status=SimpleNamespace(value="new"))
        self.submitted_at = datetime.now(timezone.utc) - timedelta(seconds=600)


class _OM:
    @property
    def recent(self):
        return [_Order()]


def test_evaluate_alerts_detects_all_three():
    items = evaluate_alerts(risk=_Risk(), streams={"crypto": _Stream()},
                            order_mgr=_OM())
    keys = {k for k, _, _ in items}
    assert "risk:daily" in keys
    assert "stream:crypto" in keys
    assert any(k.startswith("order_stale:") for k in keys)
    # danger level on breaker
    assert any(level == "danger" for k, level, _ in items if k == "risk:daily")


def test_evaluate_alerts_quiet_when_healthy():
    class OkRisk:
        def snapshot(self):
            return {"daily_halted": False, "weekly_halted": False,
                    "daily_loss_pct": 0, "weekly_loss_pct": 0}

    class OkStream:
        running = True

    assert evaluate_alerts(risk=OkRisk(), streams={"crypto": OkStream()},
                           order_mgr=None) == []
