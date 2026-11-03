"""Offline, deterministic fake market for demos and tests (``--offline``).

Generates a random-walk price series with trending and choppy phases, aligned
to real wall-clock candle boundaries, so the paper loop and the backtester can
run with no internet connection and no API keys. Prices are NOT real.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import pandas as pd

from exchange.base import ExchangeClient, MarketInfo, ohlcv_to_frame, timeframe_to_seconds


def generate_candles(start_ms: int, count: int, timeframe: str, seed: int = 42, start_price: float = 60000.0) -> pd.DataFrame:
    tf_ms = timeframe_to_seconds(timeframe) * 1000
    rng = np.random.default_rng(seed)
    # Regime-switching drift so that trend-following signals actually appear.
    drift = np.repeat(rng.normal(0, 0.0004, size=count // 60 + 1), 60)[:count]
    rets = drift + rng.normal(0, 0.003, size=count)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    spread = np.abs(rng.normal(0, 0.002, size=count))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    vol = rng.lognormal(3, 0.5, size=count)
    ts = start_ms + np.arange(count) * tf_ms
    rows = np.column_stack([ts, open_, high, low, close, vol]).tolist()
    return ohlcv_to_frame(rows)


class SyntheticExchange(ExchangeClient):
    name = "synthetic"
    supports_live = False

    def __init__(self, timeframe: str = "15m", seed: int = 42, history: int = 5000) -> None:
        self.timeframe = timeframe
        self.tf_ms = timeframe_to_seconds(timeframe) * 1000
        now = int(time.time() * 1000)
        current_open = now - now % self.tf_ms
        # The last generated candle is the one currently forming.
        start = current_open - (history - 1) * self.tf_ms
        self.df = generate_candles(start, history, timeframe, seed)
        self._tick = 0

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int, since_ms: Optional[int] = None) -> pd.DataFrame:
        df = self.df if since_ms is None else self.df[self.df["timestamp"] >= since_ms]
        return (df.head(limit) if since_ms is not None else df.tail(limit)).reset_index(drop=True)

    def fetch_last_price(self, symbol: str) -> float:
        # Wiggle around the last close so the monitor loop sees moving prices.
        self._tick += 1
        last = float(self.df["close"].iloc[-1])
        return last * (1 + 0.003 * np.sin(self._tick / 3))

    def market_info(self, symbol: str) -> MarketInfo:
        base, quote = symbol.split("/")
        return MarketInfo(symbol, base, quote, amount_step=1e-6, price_step=0.01, min_amount=1e-5, min_cost=5.0, taker_fee_pct=0.1)
