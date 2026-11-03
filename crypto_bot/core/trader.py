"""The trader: one place where data, decisions, risk checks and orders meet.

Used by both the live/paper runner and the backtester, so the strategy and risk
logic you backtest is literally the same code that trades.

Order of precedence each cycle:
  1. Kill switch / shutdown flags         (stop everything)
  2. Stop-loss / take-profit exits        (always run, even when trading is halted)
  3. Drawdown and daily-loss limits       (may halt new entries or shut down)
  4. Decision engine -> risk manager -> order
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from alerts.telegram import Notifier
from config.settings import Settings
from core.broker import Broker
from exchange.base import ExchangeClient, ExchangeError, MarketInfo, TransientExchangeError
from risk.manager import (
    RiskManager,
    RiskResult,
    RiskState,
    check_exit,
    daily_loss_hit,
    drawdown_hit,
    drawdown_pct,
    stop_take_prices,
)
from storage.db import Position, Store, day_of, iso, utcnow
from strategy.decision import Decision
from strategy.engines import DecisionEngine
from strategy.snapshot import PositionView, build_snapshot

log = logging.getLogger(__name__)


class Trader:
    def __init__(self, settings: Settings, exchange: ExchangeClient, engine: DecisionEngine, store: Store,
                 broker: Broker, notifier: Notifier, *, fee_pct_estimate: float = 0.1,
                 use_kill_switch: bool = True) -> None:
        self.s = settings
        self.ex = exchange
        self.engine = engine
        self.store = store
        self.broker = broker
        self.notify = notifier
        self.risk = RiskManager(settings.risk)
        self.fee_pct_estimate = fee_pct_estimate
        self.use_kill_switch = use_kill_switch  # backtests ignore the STOP file
        self.last_prices: dict[str, float] = {}
        self._infos: dict[str, MarketInfo] = {}
        if store.get("peak_equity") is None:
            store.set("peak_equity", settings.risk.capital_usdt)

    # ----------------------------------------------------------------- state
    def info(self, symbol: str) -> MarketInfo:
        if symbol not in self._infos:
            self._infos[symbol] = self.ex.market_info(symbol)
        return self._infos[symbol]

    def unrealized(self) -> float:
        total = 0.0
        for p in self.store.open_positions():
            price = self.last_prices.get(p.symbol, p.entry_price)
            total += p.unrealized_pnl(price, self.fee_pct_estimate)
        return total

    def equity(self) -> float:
        return self.s.risk.capital_usdt + self.store.realized_pnl_total() + self.unrealized()

    def todays_pnl(self, now: datetime) -> float:
        return self.store.realized_pnl_on(day_of(now)) + self.unrealized()

    def peak_equity(self) -> float:
        return self.store.get_float("peak_equity", self.s.risk.capital_usdt)

    def daily_halted(self, now: datetime) -> bool:
        return self.store.get("daily_halt_day") == day_of(now)

    def shutdown_reason(self) -> Optional[str]:
        return self.store.get("shutdown") or None

    def kill_switch_active(self) -> bool:
        return self.use_kill_switch and self.s.kill_switch_path.exists()

    def risk_state(self, symbol: str, now: datetime) -> RiskState:
        return RiskState(
            has_open_position=len(self.store.open_positions(symbol)) >= self.s.risk.max_open_positions_per_symbol,
            trades_today=self.store.trades_opened_on(day_of(now)),
            todays_pnl=self.todays_pnl(now),
            equity=self.equity(),
            peak_equity=self.peak_equity(),
            quote_available=self.broker.quote_available(),
            kill_switch=self.kill_switch_active(),
            daily_halted=self.daily_halted(now),
        )

    # ---------------------------------------------------------------- limits
    def enforce_limits(self, now: datetime) -> Optional[str]:
        """Update the equity peak and apply drawdown / daily-loss limits.

        Returns "shutdown" if the bot must stop completely.
        """
        eq = self.equity()
        peak = self.peak_equity()
        if eq > peak:
            self.store.set("peak_equity", eq)
            peak = eq
        if drawdown_hit(eq, peak, self.s.risk):
            self.trigger_shutdown(
                f"max drawdown reached: equity {eq:.2f} is {drawdown_pct(eq, peak):.2f}% below peak {peak:.2f} "
                f"(limit {self.s.risk.max_drawdown_pct}%)", now,
                close_positions=self.s.risk.close_positions_on_shutdown)
            return "shutdown"
        if not self.daily_halted(now) and daily_loss_hit(self.todays_pnl(now), self.s.risk):
            self.store.set("daily_halt_day", day_of(now))
            self.notify.send("shutdown", f"Daily loss limit hit (today's P&L {self.todays_pnl(now):.2f} USDT). "
                                         f"No new trades until 00:00 UTC. Open positions keep their SL/TP.")
        return None

    def trigger_shutdown(self, reason: str, now: datetime, *, close_positions: bool) -> None:
        log.critical("SHUTDOWN: %s", reason)
        self.store.set("shutdown", reason)
        self.store.log_event("CRITICAL", reason)
        if close_positions:
            self.close_all("shutdown", now)
        self.notify.send("shutdown", f"Bot shut down: {reason}. Positions closed: {close_positions}. "
                                     f"Run `python main.py reset-shutdown` after reviewing.")

    def close_all(self, reason: str, now: datetime) -> None:
        for pos in self.store.open_positions():
            price = self.last_prices.get(pos.symbol)
            if price is None:
                try:
                    price = self.ex.fetch_last_price(pos.symbol)
                except Exception as exc:  # noqa: BLE001
                    log.error("cannot price %s for close: %s", pos.symbol, exc)
                    price = pos.entry_price
            self.close_trade(pos, reason, price, now)

    # ----------------------------------------------------------------- exits
    def check_exits(self, symbol: str, now: datetime, *, price: Optional[float] = None, high: Optional[float] = None,
                    low: Optional[float] = None, open_: Optional[float] = None) -> list[Position]:
        """Close positions whose stop-loss or take-profit was reached.

        Live/paper: pass the current ``price``. Backtest: pass the candle's
        ``open_``/``high``/``low``; if the candle opened beyond a level (a gap)
        the fill uses the open price, which is what would really happen.
        """
        closed: list[Position] = []
        if price is not None:
            self.last_prices[symbol] = price
        for pos in self.store.open_positions(symbol):
            hit = check_exit(pos.stop_loss_price, pos.take_profit_price, price=price, high=high, low=low)
            if not hit:
                continue
            reason, trigger = hit
            if price is not None:
                fill_hint = price
            elif reason == "stop_loss":
                fill_hint = min(trigger, open_) if open_ is not None else trigger
            else:
                fill_hint = max(trigger, open_) if open_ is not None else trigger
            done = self.close_trade(pos, reason, fill_hint, now)
            if done:
                closed.append(done)
        return closed

    # ---------------------------------------------------------------- orders
    def open_trade(self, symbol: str, result: RiskResult, price: float, decision: Decision, now: datetime) -> dict[str, Any]:
        info = self.info(symbol)
        try:
            fill = self.broker.buy(info, float(result.size_usdt or 0), price)
        except TransientExchangeError as exc:
            msg = f"BUY {symbol}: order status UNKNOWN after network error - check the exchange manually! {exc}"
            log.critical(msg)
            self.notify.send("error", msg)
            return {"status": "unknown", "error": str(exc)}
        except ExchangeError as exc:
            log.error("BUY %s rejected: %s", symbol, exc)
            self.notify.send("error", f"BUY {symbol} rejected: {exc}")
            return {"status": "rejected", "error": str(exc)}
        if not fill.filled:
            log.warning("BUY %s not filled (%s)", symbol, fill.status)
            return {"status": fill.status, "order_id": fill.order_id}
        sl_price, tp_price = stop_take_prices(fill.price, result.stop_loss_pct or 0, result.take_profit_pct or 0)
        pos = self.store.open_position(
            symbol=symbol, amount=fill.amount, entry_price=fill.price, entry_quote=fill.net_quote,
            entry_fee=fill.fee_quote, sl_pct=result.stop_loss_pct or 0, tp_pct=result.take_profit_pct or 0,
            sl_price=sl_price, tp_price=tp_price, order_id=fill.order_id, reason=decision.reason, opened_at=iso(now),
        )
        self.notify.send("trade", f"BUY {symbol} {fill.amount:.8g} @ {fill.price:.6g} (cost {fill.net_quote:.2f} USDT, "
                                  f"{'PARTIAL ' if fill.status == 'partial' else ''}SL {sl_price:.6g} / TP {tp_price:.6g}) "
                                  f"conf {decision.confidence:.2f}: {decision.reason}")
        return {"status": fill.status, "order_id": fill.order_id, "position_id": pos.id, "amount": fill.amount,
                "price": fill.price, "cost": fill.net_quote, "fee": fill.fee_quote, "sl": sl_price, "tp": tp_price}

    def close_trade(self, pos: Position, reason: str, price_hint: float, now: datetime) -> Optional[Position]:
        info = self.info(pos.symbol)
        try:
            fill = self.broker.sell(info, pos.amount, price_hint)
        except (ExchangeError, TransientExchangeError) as exc:
            msg = f"SELL {pos.symbol} (position {pos.id}, {reason}) failed: {exc}"
            log.error(msg)
            self.notify.send("error", msg)
            return None
        if not fill.filled:
            self.notify.send("error", f"SELL {pos.symbol} not filled ({fill.status}); will retry next check")
            return None
        remainder = pos.amount - fill.amount
        sellable = remainder > 0 and (
            (not info.min_amount or remainder >= info.min_amount)
            and (not info.min_cost or remainder * fill.price >= info.min_cost)
        )
        if fill.amount < pos.amount * 0.99 and sellable:
            closed, rest = self.store.partial_close(pos.id, amount_sold=fill.amount, exit_price=fill.price,
                                                    exit_quote=fill.net_quote, exit_fee=fill.fee_quote,
                                                    order_id=fill.order_id, reason=reason)
            self.notify.send("trade", f"PARTIAL SELL {pos.symbol}: {fill.amount:.8g} sold, {rest.amount:.8g} still open")
        else:
            # Tiny unsellable leftovers ("dust") are written off in the P&L.
            closed = self.store.close_position(pos.id, exit_price=fill.price, exit_quote=fill.net_quote,
                                               exit_fee=fill.fee_quote, order_id=fill.order_id, reason=reason,
                                               closed_at=iso(now))
        self.notify.send("trade", f"SELL {pos.symbol} {fill.amount:.8g} @ {fill.price:.6g} ({reason}) "
                                  f"P&L {closed.pnl:+.4f} USDT ({closed.pnl_pct:+.2f}%)")
        return closed

    # -------------------------------------------------------------- decision
    def decision_cycle(self, symbol: str, candles: pd.DataFrame, now: datetime,
                       last_price: Optional[float] = None) -> dict[str, Any]:
        """Run one full decide -> validate -> execute cycle for a symbol."""
        price = float(last_price if last_price is not None else candles["close"].iloc[-1])
        self.last_prices[symbol] = price
        open_pos = self.store.open_positions(symbol)
        pview = None
        if open_pos:
            p = open_pos[0]
            pview = PositionView(entry_price=p.entry_price, amount=p.amount, stop_loss_price=round(p.stop_loss_price, 8),
                                 take_profit_price=round(p.take_profit_price, 8),
                                 unrealized_pnl_pct=round((price / p.entry_price - 1) * 100, 3), opened_at=p.opened_at)
        try:
            snap = build_snapshot(symbol, self.s.exchange.timeframe, candles, price, pview, self.todays_pnl(now),
                                  self.store.trades_opened_on(day_of(now)), self.s.llm.prompt_candles)
        except ValueError as exc:
            log.warning("%s: cannot build snapshot: %s", symbol, exc)
            self.store.log_cycle(symbol=symbol, engine=self.engine.name, last_price=price,
                                 parse_error=f"snapshot: {exc}", action="NONE", ts=iso(now))
            return {"action": "NONE", "error": str(exc)}

        try:
            decision = self.engine.decide(snap)
        except Exception as exc:  # noqa: BLE001 - a broken engine must never crash the loop
            log.exception("decision engine failed")
            decision = Decision.hold(f"engine error: {exc}")
        raw = getattr(self.engine, "last_raw", None)
        perr = getattr(self.engine, "last_error", None)

        result = self.risk.evaluate(decision, self.risk_state(symbol, now), self.info(symbol), price)
        order: Optional[dict[str, Any]] = None
        if result.approved and result.action == "OPEN":
            order = self.open_trade(symbol, result, price, decision, now)
        elif result.approved and result.action == "CLOSE":
            for pos in open_pos:
                closed = self.close_trade(pos, "signal_sell", price, now)
                order = {"status": "closed" if closed else "failed", "position_id": pos.id,
                         "pnl": closed.pnl if closed else None}

        log.info("%s %s price=%.6g decision=%s(%.2f) risk=%s %s", iso(now), symbol, price, decision.action,
                 decision.confidence, "APPROVED " + result.action if result.approved else "REJECTED",
                 "; ".join(result.reasons))
        self.store.log_cycle(symbol=symbol, engine=self.engine.name, last_price=price, snapshot=snap.to_dict(),
                             llm_raw=raw, decision=decision.model_dump(), parse_error=perr, risk=result.to_dict(),
                             action=result.action if result.approved else "NONE", order=order, ts=iso(now))
        return {"decision": decision.model_dump(), "risk": result.to_dict(), "order": order}

    # --------------------------------------------------------------- summary
    def daily_summary(self, day: str) -> str:
        closed = [p for p in self.store.closed_positions() if (p.closed_at or "")[:10] == day]
        pnl = sum(p.pnl or 0 for p in closed)
        wins = sum(1 for p in closed if (p.pnl or 0) > 0)
        usage = self.store.llm_usage(day)
        return (f"Daily summary {day} UTC: {len(closed)} closed trades ({wins} wins), realised P&L {pnl:+.4f} USDT, "
                f"equity {self.equity():.2f} USDT, open positions {len(self.store.open_positions())}, "
                f"Claude calls {usage['calls']} (~${usage['cost_usd']:.4f})")


def now_utc() -> datetime:
    return utcnow()
