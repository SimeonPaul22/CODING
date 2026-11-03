"""MarketSnapshot: everything a decision engine is allowed to see for one cycle."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import pandas as pd

from strategy.indicators import compute_indicators, summarize_candles


@dataclass
class PositionView:
    entry_price: float
    amount: float
    stop_loss_price: float
    take_profit_price: float
    unrealized_pnl_pct: float
    opened_at: str


@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    timestamp_utc: str
    last_price: float
    indicators: dict[str, Optional[float]]
    candles: dict[str, Any]
    position: Optional[PositionView] = None
    todays_pnl_usdt: float = 0.0
    trades_today: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_snapshot(symbol: str, timeframe: str, candles: pd.DataFrame, last_price: float,
                   position: Optional[PositionView], todays_pnl: float, trades_today: int,
                   prompt_candles: int = 12) -> MarketSnapshot:
    ts = pd.Timestamp(int(candles["timestamp"].iloc[-1]), unit="ms", tz="UTC")
    return MarketSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        timestamp_utc=ts.isoformat(),
        last_price=float(last_price),
        indicators=compute_indicators(candles),
        candles=summarize_candles(candles, prompt_candles),
        position=position,
        todays_pnl_usdt=round(todays_pnl, 4),
        trades_today=trades_today,
    )
