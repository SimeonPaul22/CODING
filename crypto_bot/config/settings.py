"""Load and validate config.yaml + .env.

All tunable settings live in config.yaml; all secrets live in .env. Both are
validated with pydantic so that a typo (e.g. ``max_trades_per_day: -1``) stops
the bot at startup instead of causing strange behaviour while trading.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_EXCHANGES = ("bybit", "binance", "bitget", "kucoin", "quidax")
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THE_RISKS"


class _Strict(BaseModel):
    # Reject unknown keys so misspelled settings are caught, not silently ignored.
    model_config = {"extra": "forbid"}


class ExchangeSettings(_Strict):
    name: Literal["bybit", "binance", "bitget", "kucoin", "quidax"] = "bybit"
    testnet: bool = False
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"], min_length=1)
    timeframe: str = "15m"
    candles_limit: int = Field(200, ge=60, le=1000)
    quote_currency: str = "USDT"
    timeout_ms: int = Field(15000, ge=1000)
    max_retries: int = Field(4, ge=0, le=10)
    quidax_live_acknowledged: bool = False

    @field_validator("symbols")
    @classmethod
    def _symbols_format(cls, v: list[str]) -> list[str]:
        for s in v:
            if "/" not in s or ":" in s:
                # "BTC/USDT:USDT" is ccxt's notation for perpetual futures.
                raise ValueError(f"'{s}' is not a spot symbol like BTC/USDT")
        return v

    @field_validator("timeframe")
    @classmethod
    def _timeframe_format(cls, v: str) -> str:
        from exchange.base import timeframe_to_seconds  # local import avoids a cycle

        timeframe_to_seconds(v)  # raises ValueError if invalid
        return v


class LLMSettings(_Strict):
    engine: Literal["claude", "rules"] = "claude"
    model: str = "claude-sonnet-5-5"
    max_tokens: int = Field(4096, ge=256, le=64000)
    effort: Literal["low", "medium", "high"] = "low"
    timeout_seconds: float = Field(60, gt=0)
    daily_cost_cap_usd: float = Field(1.0, ge=0)
    input_price_per_mtok: float = Field(2.0, ge=0)
    output_price_per_mtok: float = Field(10.0, ge=0)
    refusal_fallback: bool = True
    structured_output: bool = True
    prompt_candles: int = Field(12, ge=3, le=50)


class RiskSettings(_Strict):
    position_size_usdt: float = Field(10, gt=0)
    max_open_positions_per_symbol: int = Field(1, ge=1, le=1)  # spec: max 1
    max_trades_per_day: int = Field(3, ge=0)
    min_confidence: float = Field(0.7, ge=0, le=1)
    default_stop_loss_pct: float = Field(2.0, gt=0, lt=100)
    default_take_profit_pct: float = Field(4.0, gt=0)
    min_stop_loss_pct: float = Field(0.5, gt=0)
    max_stop_loss_pct: float = Field(5.0, gt=0, lt=100)
    min_take_profit_pct: float = Field(1.0, gt=0)
    max_take_profit_pct: float = Field(10.0, gt=0)
    min_reward_risk: float = Field(1.0, ge=0)
    capital_usdt: float = Field(100, gt=0)
    daily_loss_limit_pct: float = Field(2.0, gt=0, le=100)
    max_drawdown_pct: float = Field(10.0, gt=0, le=100)
    close_positions_on_shutdown: bool = True
    kill_switch_file: str = "STOP"
    kill_switch_action: Literal["close_and_exit", "exit_only"] = "close_and_exit"

    @model_validator(mode="after")
    def _bounds_consistent(self) -> "RiskSettings":
        if not self.min_stop_loss_pct <= self.default_stop_loss_pct <= self.max_stop_loss_pct:
            raise ValueError("default_stop_loss_pct must lie within [min_stop_loss_pct, max_stop_loss_pct]")
        if not self.min_take_profit_pct <= self.default_take_profit_pct <= self.max_take_profit_pct:
            raise ValueError("default_take_profit_pct must lie within [min_take_profit_pct, max_take_profit_pct]")
        if self.default_take_profit_pct < self.default_stop_loss_pct * self.min_reward_risk:
            raise ValueError("default take-profit violates min_reward_risk")
        if self.position_size_usdt > self.capital_usdt:
            raise ValueError("position_size_usdt cannot exceed capital_usdt")
        return self


class PaperSettings(_Strict):
    fee_pct: float = Field(0.1, ge=0, le=5)
    slippage_pct: float = Field(0.05, ge=0, le=5)


class LoopSettings(_Strict):
    monitor_interval_seconds: int = Field(30, ge=5)


class BacktestSettings(_Strict):
    start: str = "2025-01-01"
    end: str = "2025-06-30"
    engine: Literal["rules", "claude"] = "rules"
    claude_sample_candles: int = Field(30, ge=1, le=2000)
    fee_pct: float = Field(0.1, ge=0, le=5)
    slippage_pct: float = Field(0.05, ge=0, le=5)


class LoggingSettings(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: str = "logs/bot.log"
    max_bytes: int = Field(5_000_000, ge=10_000)
    backup_count: int = Field(5, ge=1)
    db_dir: str = "data"


class AlertSettings(_Strict):
    telegram_enabled: bool = False


class Secrets(BaseModel):
    """Values read from the environment / .env. Never logged."""

    exchange_api_key: Optional[str] = None
    exchange_api_secret: Optional[str] = None
    exchange_api_password: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    confirm_live_trading: Optional[str] = None

    def __repr__(self) -> str:  # keep secrets out of tracebacks / logs
        return "Secrets(<redacted>)"

    __str__ = __repr__


class Settings(_Strict):
    mode: Literal["backtest", "paper", "live"] = "paper"
    live_trading_enabled: bool = False
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    paper: PaperSettings = Field(default_factory=PaperSettings)
    loop: LoopSettings = Field(default_factory=LoopSettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    # Filled from the environment, not from YAML.
    secrets: Secrets = Field(default_factory=Secrets, exclude=True)

    def path(self, rel: str) -> Path:
        """Resolve a path from config relative to the project folder."""
        p = Path(rel)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def kill_switch_path(self) -> Path:
        return self.path(self.risk.kill_switch_file)

    def db_path(self, mode: Optional[str] = None) -> Path:
        return self.path(self.logging.db_dir) / f"bot_{mode or self.mode}.db"


def _env(name: str) -> Optional[str]:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else None


def load_settings(config_path: Optional[str | Path] = None, env_path: Optional[str | Path] = None) -> Settings:
    """Read config.yaml and .env, validate, and return a Settings object."""
    cfg_file = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
    if not cfg_file.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_file}")
    with cfg_file.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError("config.yaml must contain a mapping at the top level")

    load_dotenv(env_path or PROJECT_ROOT / ".env", override=False)
    settings = Settings(**raw)
    settings.secrets = Secrets(
        exchange_api_key=_env("EXCHANGE_API_KEY"),
        exchange_api_secret=_env("EXCHANGE_API_SECRET"),
        exchange_api_password=_env("EXCHANGE_API_PASSWORD"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        confirm_live_trading=_env("CONFIRM_LIVE_TRADING"),
    )
    return settings
