"""Indicators, config loading, live-mode gating, backtest smoke test."""
import numpy as np
import pandas as pd
import pytest

from config.settings import LIVE_CONFIRM_PHRASE, Settings, load_settings
from exchange.base import drop_unclosed_candle, ohlcv_to_frame, timeframe_to_seconds
from strategy.indicators import compute_indicators, rsi
from tests.conftest import make_candles


def test_indicators_on_uptrend():
    ind = compute_indicators(make_candles(120, step=0.5))
    assert ind["ema20"] > ind["ema50"]
    assert ind["rsi14"] > 70
    assert ind["atr14"] > 0 and ind["macd"] > 0
    assert ind["volume_change_pct"] == 0


def test_rsi_bounds():
    x = np.random.default_rng(0).normal(0, 1, 500).cumsum() + 100
    r = rsi(x)
    assert np.nanmin(r) >= 0 and np.nanmax(r) <= 100
    assert rsi(np.full(50, 5.0))[-1] == 50


def test_too_few_candles():
    with pytest.raises(ValueError):
        compute_indicators(make_candles(30))


def test_bad_rows_dropped():
    df = ohlcv_to_frame([[1, 1, 2, 0.5, 1.5, 10], [2, None, 2, 1, 1, 1], [3, -1, 2, 1, 1, 1], [1, 1, 2, 0.5, 1.5, 10]])
    assert list(df["timestamp"]) == [1]


def test_unclosed_candle_dropped():
    df = make_candles(5, start_ms=0, tf_ms=900_000)
    assert len(drop_unclosed_candle(df, "15m", now_ms=4 * 900_000 + 10)) == 4


def test_timeframes():
    assert timeframe_to_seconds("15m") == 900 and timeframe_to_seconds("4h") == 14400
    with pytest.raises(ValueError):
        timeframe_to_seconds("15x")


def test_default_mode_is_paper_and_config_file_loads():
    assert Settings().mode == "paper"
    s = load_settings()
    assert s.mode == "paper" and s.live_trading_enabled is False
    assert s.exchange.name == "bybit" and s.llm.model == "claude-sonnet-5-5"


def test_futures_symbols_rejected():
    from pydantic import ValidationError
    from config.settings import ExchangeSettings
    with pytest.raises(ValidationError):
        ExchangeSettings(symbols=["BTC/USDT:USDT"])


def test_unknown_config_keys_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(leverage=10)


def test_live_requires_flag_and_confirmation(settings, monkeypatch):
    from core.runner import StartupError, confirm_live
    settings.mode = "live"
    with pytest.raises(StartupError):
        confirm_live(settings)                        # flag off
    settings.live_trading_enabled = True
    monkeypatch.setattr("sys.stdin", None)            # simulate no terminal (systemd)
    with pytest.raises(StartupError):
        confirm_live(settings)                        # no env confirmation
    settings.secrets.confirm_live_trading = LIVE_CONFIRM_PHRASE
    confirm_live(settings)                            # ok


def test_live_prompt_must_match(settings, monkeypatch):
    from core.runner import StartupError, confirm_live

    class TTY:
        def isatty(self):
            return True

    settings.live_trading_enabled = True
    monkeypatch.setattr("sys.stdin", TTY())
    with pytest.raises(StartupError):
        confirm_live(settings, input_fn=lambda _: "yes")
    confirm_live(settings, input_fn=lambda _: LIVE_CONFIRM_PHRASE)


def test_backtest_smoke(settings):
    from backtest.engine import run_backtest
    from exchange.synthetic import generate_candles
    from strategy.engines import RuleBasedEngine
    candles = {"BTC/USDT": generate_candles(1_700_000_000_000, 1500, "15m", seed=3)}
    res = run_backtest(settings, candles, RuleBasedEngine())
    r = res.report
    assert r["trades"] > 0 and r["open_positions"] == 0
    assert 0 <= r["win_rate_pct"] <= 100
    assert r["fees_usdt"] > 0
    assert "BTC/USDT" in r["buy_and_hold"]
    # Max trades per day is honoured in the backtest too.
    per_day = {}
    for p in res.store.closed_positions():
        per_day[p.opened_at[:10]] = per_day.get(p.opened_at[:10], 0) + 1
    assert max(per_day.values()) <= settings.risk.max_trades_per_day


def test_kill_switch_mid_run_closes_positions_and_stops(settings):
    from core.runner import Runner
    from strategy.engines import RuleBasedEngine
    r = Runner(settings, offline=True, max_iterations=1, engine=RuleBasedEngine())
    t = r.trader
    price = r.ex.fetch_last_price("BTC/USDT")
    fill = t.broker.buy(t.info("BTC/USDT"), 10, price)
    t.store.open_position(symbol="BTC/USDT", amount=fill.amount, entry_price=fill.price, entry_quote=fill.net_quote,
                          entry_fee=fill.fee_quote, sl_pct=2, tp_pct=4, sl_price=fill.price * 0.5,
                          tp_price=fill.price * 2, order_id="x", reason="test")
    settings.kill_switch_path.write_text("")
    assert r.iteration() is False
    assert t.store.open_positions() == []
    assert t.store.closed_positions()[0].exit_reason == "kill_switch"
    assert r.run() == 3  # refuses to start while STOP exists
