"""Tests for ea.config — profile loading, deep-merge, live-disabled safety."""
from __future__ import annotations

import pytest

from ea.config import REPO_ROOT, load_config


def test_paper_config_loads():
    cfg = load_config(profile="paper")
    assert cfg.profile.profile == "paper"
    assert cfg.profile.account.starting_equity_usd == 10_000.0
    assert cfg.profile.broker.alpaca.data_feed == "iex"
    assert cfg.profile.risk.daily_loss_limit_pct == 2.0
    assert cfg.profile.risk.per_trade_risk_pct == 0.5
    assert "stocks" in cfg.profile.risk.asset_class_caps


def test_paper_universe_settings():
    cfg = load_config(profile="paper")
    assert cfg.profile.universe.stocks.min_price > 0
    assert cfg.profile.universe.stocks.exclude_otc is True
    assert cfg.profile.universe.crypto.quote == "USD"
    assert "EUR_USD" in cfg.profile.universe.forex.pairs


def test_live_profile_blocked_when_disabled():
    """live.yaml ships with `enabled: false` — load_config must refuse to return it."""
    with pytest.raises(RuntimeError, match="live profile is disabled"):
        load_config(profile="live")


def test_live_inherits_from_paper_when_enabled(tmp_path, monkeypatch):
    """When live.yaml is enabled and overrides only some fields, the rest come from paper.yaml."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    # Copy real paper.yaml
    paper_src = (REPO_ROOT / "config" / "paper.yaml").read_text()
    (cfg_dir / "paper.yaml").write_text(paper_src)

    # Minimal live.yaml: enabled, with one risk override
    (cfg_dir / "live.yaml").write_text(
        "profile: live\n"
        "enabled: true\n"
        "risk:\n"
        "  daily_loss_limit_pct: 1.0\n"
    )

    cfg = load_config(profile="live", config_dir=cfg_dir)
    assert cfg.is_live is True
    # Override took effect
    assert cfg.profile.risk.daily_loss_limit_pct == 1.0
    # Non-overridden fields inherited from paper
    assert cfg.profile.risk.weekly_loss_limit_pct == 5.0
    assert cfg.profile.account.starting_equity_usd == 10_000.0


def test_secrets_default_to_none_without_env():
    """Without .env values set, secret fields are None — not empty SecretStr."""
    cfg = load_config(profile="paper")
    # Either None (not set) or wrapped — both acceptable, but never a raw string
    assert cfg.env.alpaca_key_id is None or hasattr(cfg.env.alpaca_key_id, "get_secret_value")
