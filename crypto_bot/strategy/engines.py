"""Decision engines. Claude is one; the rule-based stand-in is another.

Both return a ``Decision``. The risk module treats them identically, so a
backtest with the rule engine exercises exactly the same risk and order code
that a live Claude-driven run does.
"""
from __future__ import annotations

from typing import Protocol

from strategy.decision import Decision
from strategy.snapshot import MarketSnapshot


class DecisionEngine(Protocol):
    name: str

    def decide(self, snap: MarketSnapshot) -> Decision:
        ...


class RuleBasedEngine:
    """Simple, free, deterministic trend-following stand-in for Claude.

    BUY  when EMA20 > EMA50 (uptrend), MACD histogram > 0 and rising, and RSI
         between 50 and 68 (momentum without being stretched).
    SELL (close) when in a position and the trend breaks (EMA20 < EMA50) or
         RSI > 75.
    HOLD otherwise. It is not meant to be profitable - it is a baseline and a
    cheap way to test the plumbing.
    """

    name = "rules"

    def decide(self, snap: MarketSnapshot) -> Decision:
        i = snap.indicators
        need = ("rsi14", "ema20", "ema50", "macd_hist", "macd_hist_prev", "atr_pct")
        if any(i.get(k) is None for k in need):
            return Decision.hold("indicators incomplete")
        atr_pct = float(i["atr_pct"])
        sl = min(max(atr_pct * 1.5, 0.5), 5.0)
        tp = sl * 2
        if snap.position is not None:
            if i["ema20"] < i["ema50"] or i["rsi14"] > 75:
                return Decision(action="SELL", confidence=0.8, reason="trend broke or RSI overbought",
                                suggested_stop_loss_pct=sl, suggested_take_profit_pct=tp)
            return Decision(action="HOLD", confidence=0.6, reason="in position, trend intact",
                            suggested_stop_loss_pct=sl, suggested_take_profit_pct=tp)
        uptrend = i["ema20"] > i["ema50"]
        momentum = i["macd_hist"] > 0 and i["macd_hist"] > i["macd_hist_prev"]
        rsi_ok = 50 <= i["rsi14"] <= 68
        if uptrend and momentum and rsi_ok:
            return Decision(action="BUY", confidence=0.75, reason="uptrend, rising MACD, RSI healthy",
                            suggested_stop_loss_pct=sl, suggested_take_profit_pct=tp)
        return Decision(action="HOLD", confidence=0.5, reason="no aligned signal",
                        suggested_stop_loss_pct=sl, suggested_take_profit_pct=tp)
