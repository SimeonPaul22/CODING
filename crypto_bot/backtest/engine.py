"""Backtester: replays historical candles through the real Trader.

Assumptions (all chosen to be conservative):
  * Decisions are made at each candle's close; a BUY fills at that close plus
    slippage, and pays the configured fee.
  * Stop-loss / take-profit are checked against the NEXT candles' high/low. If
    one candle touches both, we assume the stop-loss was hit first. If a candle
    opens beyond a level (a gap), we fill at the open.
  * Positions still open at the end are closed at the last close.
  * Fees and slippage are applied on both entry and exit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from alerts.telegram import Notifier
from config.settings import Settings
from core.broker import SimBroker
from core.report import performance_report
from core.trader import Trader
from exchange.base import ExchangeClient, MarketInfo, timeframe_to_seconds
from storage.db import Store
from strategy.engines import DecisionEngine
from strategy.indicators import MIN_CANDLES

log = logging.getLogger(__name__)


class _StaticMarket(ExchangeClient):
    """Exchange stand-in for backtests: market rules only, no network."""

    name = "backtest"
    supports_live = False

    def __init__(self, infos: dict[str, MarketInfo]) -> None:
        self.infos = infos

    def fetch_ohlcv(self, *a, **k):  # pragma: no cover - never used
        raise NotImplementedError

    def fetch_last_price(self, symbol: str) -> float:  # pragma: no cover
        raise NotImplementedError

    def market_info(self, symbol: str) -> MarketInfo:
        return self.infos[symbol]


@dataclass
class BacktestResult:
    report: dict
    equity_curve: pd.DataFrame
    store: Store
    candles: dict[str, pd.DataFrame] = field(default_factory=dict)


def default_market_info(symbol: str, fee_pct: float) -> MarketInfo:
    base, quote = symbol.split("/")
    return MarketInfo(symbol, base, quote, amount_step=1e-8, price_step=1e-8, min_amount=None, min_cost=1.0,
                      taker_fee_pct=fee_pct)


def run_backtest(settings: Settings, candles: dict[str, pd.DataFrame], engine: DecisionEngine, *,
                 market_infos: Optional[dict[str, MarketInfo]] = None, db_path: Optional[Path] = None,
                 decide_from_index: int = 0) -> BacktestResult:
    """Run the strategy over ``candles`` (one DataFrame per symbol).

    ``decide_from_index``: skip decisions before this candle index (used to
    limit real Claude calls to a small recent sample). Earlier candles still
    serve as indicator history.
    """
    bt = settings.backtest
    if db_path is not None and db_path.exists():
        db_path.unlink()  # every backtest starts from a clean slate
    store = Store(db_path or ":memory:")
    infos = market_infos or {s: default_market_info(s, bt.fee_pct) for s in candles}
    broker = SimBroker(store, settings.risk.capital_usdt, bt.fee_pct, bt.slippage_pct)
    trader = Trader(settings, _StaticMarket(infos), engine, store, broker, Notifier(False, None, None),
                    fee_pct_estimate=bt.fee_pct, use_kill_switch=False)
    tf_ms = timeframe_to_seconds(settings.exchange.timeframe) * 1000
    window = settings.exchange.candles_limit

    # Merge all symbols onto one timeline.
    timeline = sorted({int(t) for df in candles.values() for t in df["timestamp"]})
    index = {s: {int(t): i for i, t in enumerate(df["timestamp"])} for s, df in candles.items()}
    curve: list[tuple[datetime, float]] = []
    stopped = False
    start_decisions = max(MIN_CANDLES, decide_from_index)

    for ts in timeline:
        now = datetime.fromtimestamp((ts + tf_ms) / 1000, tz=timezone.utc)  # candle close time
        for symbol, df in candles.items():
            i = index[symbol].get(ts)
            if i is None:
                continue
            c = df.iloc[i]
            trader.check_exits(symbol, now, high=float(c.high), low=float(c.low), open_=float(c.open))
            trader.last_prices[symbol] = float(c.close)
        if trader.enforce_limits(now) == "shutdown":
            stopped = True
            curve.append((now, trader.equity()))
            break
        for symbol, df in candles.items():
            i = index[symbol].get(ts)
            if i is None or i < start_decisions:
                continue
            trader.decision_cycle(symbol, df.iloc[max(0, i - window + 1): i + 1], now)
        curve.append((now, trader.equity()))

    if not stopped:
        end = curve[-1][0] if curve else datetime.now(timezone.utc)
        for pos in store.open_positions():
            trader.close_trade(pos, "end_of_backtest", trader.last_prices[pos.symbol], end)
        if curve:
            curve.append((end, trader.equity()))

    eq = pd.DataFrame(curve, columns=["time", "equity"])
    bh = {}
    for s, df in candles.items():
        d = df.iloc[start_decisions:] if len(df) > start_decisions else df
        bh[s] = (float(d["close"].iloc[0]), float(d["close"].iloc[-1]))
    report = performance_report(store, settings.risk.capital_usdt, bh, equity_curve=eq["equity"].tolist(),
                                fee_pct=bt.fee_pct)
    if stopped:
        report["note"] = "backtest stopped early: max drawdown shutdown triggered"
    return BacktestResult(report=report, equity_curve=eq, store=store, candles=candles)
