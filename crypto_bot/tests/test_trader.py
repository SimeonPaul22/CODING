"""End-to-end trader behaviour with the simulated broker (no network)."""
from datetime import timedelta

import pytest

from alerts.telegram import Notifier
from core.broker import SimBroker
from core.trader import Trader
from exchange.base import ExchangeClient, Fill
from storage.db import Store
from tests.conftest import make_candles, make_decision


class FakeExchange(ExchangeClient):
    name = "fake"

    def __init__(self, info):
        self._info = info
        self.price = 100.0

    def fetch_ohlcv(self, *a, **k):
        raise NotImplementedError

    def fetch_last_price(self, symbol):
        return self.price

    def market_info(self, symbol):
        return self._info


class ScriptedEngine:
    name = "scripted"

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def decide(self, snap):
        return self.decisions.pop(0) if self.decisions else make_decision("HOLD", 0.0)


@pytest.fixture
def make_trader(settings, info, tmp_path):
    def _make(decisions, fee=0.1, slip=0.0):
        store = Store(tmp_path / "t.db")
        broker = SimBroker(store, settings.risk.capital_usdt, fee, slip)
        t = Trader(settings, FakeExchange(info), ScriptedEngine(decisions), store, broker,
                   Notifier(False, None, None), fee_pct_estimate=fee)
        return t, store, broker
    return _make


def test_buy_then_stop_loss(make_trader, now):
    t, store, broker = make_trader([make_decision("BUY", 0.9, 2.0, 4.0)])
    candles = make_candles()
    price = float(candles["close"].iloc[-1])
    out = t.decision_cycle("BTC/USDT", candles, now)
    assert out["order"]["status"] == "closed"
    pos = store.open_positions()[0]
    assert pos.stop_loss_price == pytest.approx(pos.entry_price * 0.98)
    assert pos.take_profit_price == pytest.approx(pos.entry_price * 1.04)
    assert pos.entry_quote == pytest.approx(pos.amount * price * 1.001)  # cost + 0.1% fee
    assert broker.quote_available() == pytest.approx(100 - pos.entry_quote)

    assert t.check_exits("BTC/USDT", now, price=price * 0.99) == []          # not yet
    closed = t.check_exits("BTC/USDT", now, price=price * 0.979)
    assert len(closed) == 1 and closed[0].exit_reason == "stop_loss"
    # Loss = ~2.1% of 10 USDT + fees on both sides
    assert closed[0].pnl == pytest.approx(pos.amount * price * 0.979 * 0.999 - pos.entry_quote)
    assert closed[0].pnl < -0.2
    assert broker.quote_available() == pytest.approx(100 + closed[0].pnl)
    assert t.equity() == pytest.approx(100 + closed[0].pnl)


def test_take_profit(make_trader, now):
    t, store, _ = make_trader([make_decision("BUY", 0.9, 2.0, 4.0)])
    candles = make_candles()
    t.decision_cycle("BTC/USDT", candles, now)
    p = float(candles["close"].iloc[-1])
    closed = t.check_exits("BTC/USDT", now, price=p * 1.05)
    assert closed[0].exit_reason == "take_profit" and closed[0].pnl > 0


def test_backtest_gap_fills_at_open(make_trader, now):
    t, store, _ = make_trader([make_decision("BUY", 0.9, 2.0, 4.0)])
    t.decision_cycle("BTC/USDT", make_candles(), now)
    pos = store.open_positions()[0]
    gap_open = pos.stop_loss_price * 0.95
    closed = t.check_exits("BTC/USDT", now, high=gap_open * 1.01, low=gap_open * 0.99, open_=gap_open)
    assert closed[0].exit_price == pytest.approx(gap_open)  # worse than the stop: realistic


def test_claude_sl_out_of_bounds_uses_default(make_trader, now):
    t, store, _ = make_trader([make_decision("BUY", 0.9, 20.0, 40.0)])
    t.decision_cycle("BTC/USDT", make_candles(), now)
    pos = store.open_positions()[0]
    assert pos.stop_loss_pct == 2.0 and pos.take_profit_pct == 4.0


def test_max_trades_per_day_across_cycles(make_trader, now, settings):
    buys = [make_decision("BUY"), make_decision("SELL"), make_decision("BUY"), make_decision("SELL"),
            make_decision("BUY"), make_decision("SELL"), make_decision("BUY")]
    t, store, _ = make_trader(buys)
    candles = make_candles()
    for _ in buys:
        t.decision_cycle("BTC/USDT", candles, now)
    assert store.trades_opened_on(now.strftime("%Y-%m-%d")) == settings.risk.max_trades_per_day
    assert store.open_positions() == []
    # Next UTC day the counter resets.
    t.engine.decisions = [make_decision("BUY")]
    t.decision_cycle("BTC/USDT", candles, now + timedelta(days=1))
    assert len(store.open_positions()) == 1


def test_low_confidence_and_garbage_never_trade(make_trader, now):
    t, store, _ = make_trader([make_decision("BUY", 0.5)])
    t.decision_cycle("BTC/USDT", make_candles(), now)
    assert store.open_positions() == []


def test_engine_exception_is_hold(make_trader, now):
    t, store, _ = make_trader([])

    def boom(snap):
        raise RuntimeError("model exploded")

    t.engine.decide = boom
    out = t.decision_cycle("BTC/USDT", make_candles(), now)
    assert out["decision"]["action"] == "HOLD" and store.open_positions() == []


def test_drawdown_shutdown_closes_positions(make_trader, now, settings):
    settings.risk.max_drawdown_pct = 0.1  # tiny, so one bad tick triggers it
    t, store, _ = make_trader([make_decision("BUY")])
    candles = make_candles()
    t.decision_cycle("BTC/USDT", candles, now)
    t.last_prices["BTC/USDT"] = float(candles["close"].iloc[-1]) * 0.985
    assert t.enforce_limits(now) == "shutdown"
    assert store.open_positions() == [] and t.shutdown_reason()


def test_daily_loss_halts_until_next_day(make_trader, now, settings):
    settings.risk.daily_loss_limit_pct = 0.1  # 0.1 USDT
    t, store, _ = make_trader([make_decision("BUY"), make_decision("BUY"), make_decision("BUY")])
    candles = make_candles()
    t.decision_cycle("BTC/USDT", candles, now)
    t.check_exits("BTC/USDT", now, price=float(candles["close"].iloc[-1]) * 0.97)  # stop-loss
    t.enforce_limits(now)
    assert t.daily_halted(now)
    t.decision_cycle("BTC/USDT", candles, now)
    assert store.open_positions() == []
    t.decision_cycle("BTC/USDT", candles, now + timedelta(days=1))
    assert len(store.open_positions()) == 1


def test_kill_switch_file(make_trader, now, settings):
    t, store, _ = make_trader([make_decision("BUY")])
    settings.kill_switch_path.write_text("stop")
    t.decision_cycle("BTC/USDT", make_candles(), now)
    assert store.open_positions() == []


def test_partial_sell_keeps_remainder_open(make_trader, now, info):
    t, store, broker = make_trader([make_decision("BUY")])
    t.decision_cycle("BTC/USDT", make_candles(), now)
    pos = store.open_positions()[0]
    half = round(pos.amount / 2, 5)

    def partial_sell(info_, amount, price_hint):
        return Fill("x", info_.symbol, "sell", half, price_hint, half * price_hint, 0.0, "partial",
                    net_quote=half * price_hint)

    broker.sell = partial_sell
    t.close_trade(pos, "signal_sell", 120.0, now)
    rest = store.open_positions()
    assert len(rest) == 1 and rest[0].amount == pytest.approx(pos.amount - half)
    closed = store.closed_positions()
    assert len(closed) == 1 and closed[0].amount == pytest.approx(half)
    assert store.trades_opened_on(now.strftime("%Y-%m-%d")) == 1  # split is not a new trade


def test_failed_buy_records_no_position(make_trader, now):
    from exchange.base import ExchangeError
    t, store, broker = make_trader([make_decision("BUY")])

    def reject(*a, **k):
        raise ExchangeError("insufficient funds")

    broker.buy = reject
    out = t.decision_cycle("BTC/USDT", make_candles(), now)
    assert out["order"]["status"] == "rejected" and store.open_positions() == []
