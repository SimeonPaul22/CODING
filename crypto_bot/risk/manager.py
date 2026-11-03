"""Hard risk rules. Enforced in code; nothing Claude says can override them.

Everything here is a pure function of its inputs (no network, no database),
which is what makes the rules easy to unit-test and identical in backtest,
paper and live modes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from config.settings import RiskSettings
from exchange.base import MarketInfo
from strategy.decision import Decision


@dataclass
class RiskState:
    """What the risk manager needs to know about the bot right now."""

    has_open_position: bool
    trades_today: int
    todays_pnl: float          # realised today + unrealised on open positions, USDT
    equity: float              # capital + all realised P&L + unrealised P&L
    peak_equity: float         # highest equity seen so far
    quote_available: float     # free USDT the bot may spend
    kill_switch: bool = False
    daily_halted: bool = False


@dataclass
class RiskResult:
    approved: bool
    action: Literal["OPEN", "CLOSE", "NONE"]
    reasons: list[str] = field(default_factory=list)
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    size_usdt: Optional[float] = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SizeResult:
    ok: bool
    cost: float = 0.0
    amount: float = 0.0
    reason: str = ""


# --------------------------------------------------------------- pure rules
def resolve_sl_tp(suggested_sl: Optional[float], suggested_tp: Optional[float], cfg: RiskSettings) -> tuple[float, float, list[str]]:
    """Use Claude's SL/TP only if inside the configured bounds, else defaults.

    If the pair would give a reward smaller than ``min_reward_risk`` x risk,
    both fall back to the defaults.
    """
    notes: list[str] = []
    sl = suggested_sl
    if sl is None or not (cfg.min_stop_loss_pct <= sl <= cfg.max_stop_loss_pct):
        notes.append(f"stop-loss {sl} outside [{cfg.min_stop_loss_pct}, {cfg.max_stop_loss_pct}] -> default {cfg.default_stop_loss_pct}")
        sl = cfg.default_stop_loss_pct
    tp = suggested_tp
    if tp is None or not (cfg.min_take_profit_pct <= tp <= cfg.max_take_profit_pct):
        notes.append(f"take-profit {tp} outside [{cfg.min_take_profit_pct}, {cfg.max_take_profit_pct}] -> default {cfg.default_take_profit_pct}")
        tp = cfg.default_take_profit_pct
    if tp < sl * cfg.min_reward_risk:
        notes.append(f"reward/risk {tp / sl:.2f} < {cfg.min_reward_risk} -> using defaults")
        sl, tp = cfg.default_stop_loss_pct, cfg.default_take_profit_pct
    return float(sl), float(tp), notes


def stop_take_prices(entry_price: float, sl_pct: float, tp_pct: float) -> tuple[float, float]:
    """Long-only: stop below entry, target above."""
    return entry_price * (1 - sl_pct / 100), entry_price * (1 + tp_pct / 100)


def check_exit(stop_price: float, take_price: float, *, price: Optional[float] = None,
               high: Optional[float] = None, low: Optional[float] = None) -> Optional[tuple[str, float]]:
    """Has the stop-loss or take-profit been reached?

    Live/paper pass the current ``price``. Backtests pass the candle's
    ``high``/``low``; if a candle touched both levels we cannot know which came
    first, so we pessimistically assume the stop-loss.
    Returns (reason, trigger_price) or None.
    """
    lo = low if low is not None else price
    hi = high if high is not None else price
    if lo is None or hi is None:
        return None
    if lo <= stop_price:
        return "stop_loss", stop_price
    if hi >= take_price:
        return "take_profit", take_price
    return None


def daily_loss_hit(todays_pnl: float, cfg: RiskSettings) -> bool:
    return todays_pnl <= -cfg.capital_usdt * cfg.daily_loss_limit_pct / 100


def drawdown_pct(equity: float, peak_equity: float) -> float:
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - equity) / peak_equity * 100)


def drawdown_hit(equity: float, peak_equity: float, cfg: RiskSettings) -> bool:
    return drawdown_pct(equity, peak_equity) >= cfg.max_drawdown_pct


def compute_order_size(size_usdt: float, price: float, info: MarketInfo, quote_available: float) -> SizeResult:
    """Turn the fixed USDT size into an order the exchange will accept.

    Checks: price sane, enough free USDT (incl. fee), amount rounded down to the
    lot size, and the exchange's minimum amount / minimum order value.
    """
    if price <= 0:
        return SizeResult(False, reason="invalid price")
    needed = size_usdt * (1 + info.taker_fee_pct / 100)
    if quote_available + 1e-9 < needed:
        return SizeResult(False, reason=f"insufficient {info.quote}: need {needed:.4f}, have {quote_available:.4f}")
    amount = info.round_amount_down(size_usdt / price)
    if amount <= 0:
        return SizeResult(False, reason="amount rounds to zero at this lot size")
    if info.min_amount and amount < info.min_amount:
        return SizeResult(False, reason=f"amount {amount} below exchange minimum {info.min_amount}")
    cost = amount * price
    if info.min_cost and cost < info.min_cost:
        return SizeResult(False, reason=f"order value {cost:.4f} below exchange minimum {info.min_cost}")
    return SizeResult(True, cost=cost, amount=amount)


# ------------------------------------------------------------------ manager
class RiskManager:
    def __init__(self, cfg: RiskSettings) -> None:
        self.cfg = cfg

    def evaluate(self, decision: Decision, state: RiskState, info: MarketInfo, price: float) -> RiskResult:
        """Decide whether a model decision may be executed."""
        cfg = self.cfg
        reasons: list[str] = []

        if state.kill_switch:
            return RiskResult(False, "NONE", ["kill switch active"])
        if drawdown_hit(state.equity, state.peak_equity, cfg):
            return RiskResult(False, "NONE", [f"max drawdown {cfg.max_drawdown_pct}% reached"])
        if decision.action == "HOLD":
            return RiskResult(False, "NONE", ["decision is HOLD"])
        if decision.confidence < cfg.min_confidence:
            return RiskResult(False, "NONE", [f"confidence {decision.confidence:.2f} < minimum {cfg.min_confidence}"])

        if decision.action == "SELL":
            if not state.has_open_position:
                # Spot only: SELL without a position would be a short. Not allowed.
                return RiskResult(False, "NONE", ["SELL ignored: no open position (spot only, no shorting)"])
            return RiskResult(True, "CLOSE", ["close position on SELL signal"])

        # BUY
        if state.has_open_position:
            reasons.append(f"already holding a position (max {cfg.max_open_positions_per_symbol} per symbol)")
        if state.trades_today >= cfg.max_trades_per_day:
            reasons.append(f"max trades per day ({cfg.max_trades_per_day}) reached")
        if state.daily_halted or daily_loss_hit(state.todays_pnl, cfg):
            reasons.append(f"daily loss limit ({cfg.daily_loss_limit_pct}% of capital) reached")
        size = compute_order_size(cfg.position_size_usdt, price, info, state.quote_available)
        if not size.ok:
            reasons.append(size.reason)
        if reasons:
            return RiskResult(False, "NONE", reasons)

        sl, tp, notes = resolve_sl_tp(decision.suggested_stop_loss_pct, decision.suggested_take_profit_pct, cfg)
        return RiskResult(True, "OPEN", notes or ["all checks passed"], stop_loss_pct=sl, take_profit_pct=tp,
                          size_usdt=cfg.position_size_usdt)
