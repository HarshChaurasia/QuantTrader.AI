"""Configuration loading.

Two layers:
1. Secrets and environment toggles come from `.env` / process env via pydantic-settings.
2. Profile config (paper.yaml or live.yaml) is loaded from the `config/` directory,
   selected by EA_PROFILE. The live profile deep-merges over paper, so live.yaml only
   needs to specify what differs.

Loaded via `get_config()` (cached). Tests can pass a custom config_dir to bypass cache.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"


# --- Secrets / env-driven settings ---

class EnvSettings(BaseSettings):
    """Loaded from .env + process env. Never logged in full; secrets wrapped in SecretStr."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Profile selection
    ea_profile: Literal["paper", "live"] = "paper"
    ea_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Alpaca
    alpaca_key_id: SecretStr | None = None
    alpaca_secret_key: SecretStr | None = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"

    # Anthropic
    anthropic_api_key: SecretStr | None = None
    # Google Gemini (free tier available at https://aistudio.google.com/app/apikey)
    gemini_api_key: SecretStr | None = None

    # OANDA (Phase C)
    oanda_api_token: SecretStr | None = None
    oanda_account_id: str | None = None
    oanda_environment: Literal["practice", "live"] = "practice"


# --- Typed profile config (loaded from YAML) ---

class AccountConfig(BaseModel):
    starting_equity_usd: float = 10_000.0


class AlpacaBrokerConfig(BaseModel):
    base_url: str
    data_url: str
    data_feed: Literal["iex", "sip"] = "iex"


class BrokerConfig(BaseModel):
    alpaca: AlpacaBrokerConfig


class RiskConfig(BaseModel):
    daily_loss_limit_pct: float
    weekly_loss_limit_pct: float
    per_position_max_pct: float
    per_trade_risk_pct: float
    max_concurrent_positions: int
    max_per_asset_class: int
    asset_class_caps: dict[str, float]
    sector_cap_pct: float
    portfolio_var_cap_pct: float


class StockUniverseConfig(BaseModel):
    min_price: float
    min_avg_dollar_volume: float
    exclude_otc: bool


class CryptoUniverseConfig(BaseModel):
    quote: str
    top_n_by_volume: int


class ForexUniverseConfig(BaseModel):
    pairs: list[str]


class UniverseConfig(BaseModel):
    stocks: StockUniverseConfig
    crypto: CryptoUniverseConfig
    forex: ForexUniverseConfig


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "gemini", "none"] = "gemini"
    cheap_model: str
    deep_model: str
    daily_spend_alert_usd: float
    relevance_threshold: float
    materiality_threshold: float


class NewsConfig(BaseModel):
    llm: LLMConfig


class RegimeFiltersConfig(BaseModel):
    vix_high_threshold: float
    btc_realized_vol_high_pct: float


class ProfileConfig(BaseModel):
    profile: Literal["paper", "live"]
    enabled: bool = True
    account: AccountConfig
    broker: BrokerConfig
    risk: RiskConfig
    universe: UniverseConfig
    news: NewsConfig
    regime_filters: RegimeFiltersConfig


# --- Top-level Config object ---

class Config(BaseModel):
    env: EnvSettings
    profile: ProfileConfig

    @property
    def is_live(self) -> bool:
        return self.profile.profile == "live"


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into `base`. Override wins on conflicts."""
    out: dict[str, Any] = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], Mapping) and isinstance(v, Mapping):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(
    profile: str | None = None,
    config_dir: Path | None = None,
) -> Config:
    """Load config without caching. Useful for tests."""
    env = EnvSettings()
    active_profile = profile or env.ea_profile
    cfg_dir = config_dir or DEFAULT_CONFIG_DIR

    paper_data = _load_yaml(cfg_dir / "paper.yaml")
    if active_profile == "live":
        live_data = _load_yaml(cfg_dir / "live.yaml")
        merged = _deep_merge(paper_data, live_data)
    else:
        merged = paper_data

    # Force the profile field to match the active selection (in case YAML disagrees)
    merged["profile"] = active_profile

    profile_cfg = ProfileConfig.model_validate(merged)

    # Live safety: explicit refusal if `enabled: false` is set in live.yaml
    if active_profile == "live" and not profile_cfg.enabled:
        raise RuntimeError(
            "live profile is disabled (config/live.yaml has `enabled: false`). "
            "Pre-live audit must be completed and this flag flipped manually before live trading."
        )

    return Config(env=env, profile=profile_cfg)


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Cached accessor. Most code uses this."""
    return load_config()


def reset_config_cache() -> None:
    """Clear the cache. Used by tests after mutating env vars."""
    get_config.cache_clear()
