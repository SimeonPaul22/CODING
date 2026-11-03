"""Shared fixtures. Tests never touch the network or real API keys."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from config.settings import Settings
from exchange.base import MarketInfo
from strategy.decision import Decision


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.logging.db_dir = str(tmp_path / "data")
    s.risk.kill_switch_file = str(tmp_path / "STOP")
    return s


@pytest.fixture
def info() -> MarketInfo:
    return MarketInfo("BTC/USDT", "BTC", "USDT", amount_step=1e-5, price_step=0.01,
                      min_amount=1e-5, min_cost=5.0, taker_fee_pct=0.1)


@pytest.fixture
def now() -> datetime:
    return datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc)


def make_decision(action: str = "BUY", confidence: float = 0.8, sl: float = 2.0, tp: float = 4.0) -> Decision:
    return Decision(action=action, confidence=confidence, reason="test",
                    suggested_stop_loss_pct=sl, suggested_take_profit_pct=tp)


def make_candles(n: int = 120, start: float = 100.0, step: float = 0.1, start_ms: int = 1_700_000_000_000,
                 tf_ms: int = 900_000) -> pd.DataFrame:
    close = start + np.arange(n) * step
    return pd.DataFrame({
        "timestamp": start_ms + np.arange(n) * tf_ms,
        "open": close - step / 2, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": np.full(n, 10.0),
    })
