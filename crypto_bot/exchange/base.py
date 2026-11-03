"""Exchange-agnostic types and the interface every exchange adapter implements.

The rest of the bot only talks to ``ExchangeClient``. That is what lets you
switch from Bybit to Binance (or to the Quidax adapter, or to the offline
synthetic feed) by changing config only.
"""
from __future__ import annotations

import logging
import math
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

import pandas as pd

log = logging.getLogger(__name__)

T = TypeVar("T")

_TF_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_to_seconds(tf: str) -> int:
    """'15m' -> 900, '1h' -> 3600, '1d' -> 86400."""
    m = re.fullmatch(r"(\d+)([mhdw])", tf.strip())
    if not m:
        raise ValueError(f"Unsupported timeframe '{tf}' (use e.g. 1m, 5m, 15m, 1h, 4h, 1d)")
    n = int(m.group(1))
    if n <= 0:
        raise ValueError("timeframe must be positive")
    return n * _TF_UNITS[m.group(2)]


@dataclass(frozen=True)
class MarketInfo:
    """Trading rules for one symbol, taken from the exchange's market data.

    amount_step / price_step: the smallest increment allowed ("lot size" and
    "tick size"). min_amount / min_cost: the smallest order the exchange
    accepts, in base units and quote units (USDT) respectively.
    """

    symbol: str
    base: str
    quote: str
    amount_step: Optional[float] = None
    price_step: Optional[float] = None
    min_amount: Optional[float] = None
    min_cost: Optional[float] = None
    taker_fee_pct: float = 0.1

    def round_amount_down(self, amount: float) -> float:
        """Round DOWN to the lot size (rounding up could exceed your balance)."""
        if not self.amount_step or self.amount_step <= 0:
            return amount
        steps = math.floor(amount / self.amount_step + 1e-9)
        # Format away float noise (e.g. 0.30000000000000004).
        decimals = max(0, -int(math.floor(math.log10(self.amount_step)))) if self.amount_step < 1 else 0
        return round(steps * self.amount_step, decimals + 2)

    def round_price(self, price: float) -> float:
        if not self.price_step or self.price_step <= 0:
            return price
        steps = round(price / self.price_step)
        decimals = max(0, -int(math.floor(math.log10(self.price_step)))) if self.price_step < 1 else 0
        return round(steps * self.price_step, decimals + 2)


@dataclass
class Fill:
    """Result of an order after the bot has finished waiting on it.

    ``amount`` is the base quantity the bot actually holds/sold, already net of
    any fee the exchange deducted in the base currency.
    """

    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    amount: float
    price: float
    cost: float  # quote value of the filled amount (amount * price before fees)
    fee_quote: float
    status: str  # "closed" | "partial" | "canceled" | "failed"
    # Quote currency that actually left (buy) or arrived in (sell) the account,
    # after quote-denominated fees. P&L = sell.net_quote - buy.net_quote.
    net_quote: float = 0.0
    client_order_id: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def filled(self) -> bool:
        return self.amount > 0 and self.status in ("closed", "partial")


@dataclass
class PermissionReport:
    checked: bool
    can_withdraw: Optional[bool]
    details: str


class ExchangeError(Exception):
    """Non-retryable exchange problem (bad symbol, insufficient funds, ...)."""


class TransientExchangeError(Exception):
    """Retryable problem (timeout, 5xx, rate limit)."""


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int,
    retry_on: tuple[type[BaseException], ...],
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    what: str = "request",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` with exponential backoff + jitter on transient errors.

    Delays: ~1s, 2s, 4s, 8s ... capped at ``max_delay``. Jitter (a small random
    extra wait) avoids hammering the exchange in lock-step after an outage.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except retry_on as exc:
            if attempt >= retries:
                log.error("%s failed after %d retries: %s", what, retries, exc)
                raise
            delay = min(max_delay, base_delay * (2**attempt)) + random.uniform(0, 0.5)
            log.warning("%s failed (%s: %s); retry %d/%d in %.1fs",
                        what, type(exc).__name__, exc, attempt + 1, retries, delay)
            sleep(delay)
            attempt += 1


OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def ohlcv_to_frame(rows: list[list[float]]) -> pd.DataFrame:
    """Convert ccxt-style [[ts_ms, o, h, l, c, v], ...] into a clean DataFrame.

    Bad rows (missing values, non-positive prices, duplicates) are dropped so
    that a glitch in exchange data cannot crash indicator code.
    """
    df = pd.DataFrame(rows or [], columns=OHLCV_COLUMNS)
    if df.empty:
        return df
    for c in OHLCV_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna()
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1) & (df["volume"] >= 0)]
    df["timestamp"] = df["timestamp"].astype("int64")
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def drop_unclosed_candle(df: pd.DataFrame, timeframe: str, now_ms: Optional[int] = None) -> pd.DataFrame:
    """Exchanges return the still-forming current candle last; drop it.

    Indicators computed on a half-finished candle change every few seconds,
    which would make decisions flicker. We only decide on closed candles.
    """
    if df.empty:
        return df
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    tf_ms = timeframe_to_seconds(timeframe) * 1000
    return df[df["timestamp"] + tf_ms <= now_ms].reset_index(drop=True)


class ExchangeClient(ABC):
    """Interface the bot uses. Implemented by ccxt, Quidax and synthetic feeds."""

    name: str = "base"
    #: True only for adapters that can place real orders.
    supports_live: bool = True

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int, since_ms: Optional[int] = None) -> pd.DataFrame:
        ...

    @abstractmethod
    def fetch_last_price(self, symbol: str) -> float:
        ...

    @abstractmethod
    def market_info(self, symbol: str) -> MarketInfo:
        ...

    # ---- private / trading endpoints (live mode only) ----
    def free_balance(self, currency: str) -> float:
        raise ExchangeError(f"{self.name}: balances not supported")

    def market_buy(self, symbol: str, quote_cost: float, client_order_id: str) -> Fill:
        raise ExchangeError(f"{self.name}: live orders not supported")

    def market_sell(self, symbol: str, amount: float, client_order_id: str) -> Fill:
        raise ExchangeError(f"{self.name}: live orders not supported")

    def check_api_permissions(self) -> PermissionReport:
        return PermissionReport(False, None, "permission check not implemented for this exchange")

    def fetch_history(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        """Download a long history by paging through fetch_ohlcv."""
        tf_ms = timeframe_to_seconds(timeframe) * 1000
        frames: list[pd.DataFrame] = []
        since = start_ms
        while since < end_ms:
            df = self.fetch_ohlcv(symbol, timeframe, limit=1000, since_ms=since)
            if df.empty:
                break
            frames.append(df)
            last = int(df["timestamp"].iloc[-1])
            if last + tf_ms <= since:  # no progress - avoid an infinite loop
                break
            since = last + tf_ms
        if not frames:
            return ohlcv_to_frame([])
        out = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp")
        return out[(out["timestamp"] >= start_ms) & (out["timestamp"] < end_ms)].reset_index(drop=True)
