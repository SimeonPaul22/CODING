"""Hard risk rules - Claude cannot override any of these."""
import pytest

from risk.manager import RiskManager, RiskState, daily_loss_hit, drawdown_hit, drawdown_pct, resolve_sl_tp
from tests.conftest import make_decision


def state(**kw) -> RiskState:
    base = dict(has_open_position=False, trades_today=0, todays_pnl=0.0, equity=100.0, peak_equity=100.0,
                quote_available=100.0, kill_switch=False, daily_halted=False)
    base.update(kw)
    return RiskState(**base)


@pytest.fixture
def rm(settings):
    return RiskManager(settings.risk)


def test_buy_approved_when_all_checks_pass(rm, info):
    r = rm.evaluate(make_decision("BUY", 0.8, 1.5, 3.0), state(), info, 50000)
    assert r.approved and r.action == "OPEN"
    assert r.size_usdt == 10 and r.stop_loss_pct == 1.5 and r.take_profit_pct == 3.0


def test_hold_never_trades(rm, info):
    r = rm.evaluate(make_decision("HOLD", 0.99), state(), info, 50000)
    assert not r.approved and r.action == "NONE"


@pytest.mark.parametrize("conf,ok", [(0.69, False), (0.7, True), (0.95, True)])
def test_min_confidence(rm, info, conf, ok):
    assert rm.evaluate(make_decision("BUY", conf), state(), info, 50000).approved is ok


def test_one_position_per_symbol(rm, info):
    r = rm.evaluate(make_decision("BUY"), state(has_open_position=True), info, 50000)
    assert not r.approved and any("already holding" in x for x in r.reasons)


def test_max_trades_per_day(rm, info):
    assert rm.evaluate(make_decision("BUY"), state(trades_today=2), info, 50000).approved
    r = rm.evaluate(make_decision("BUY"), state(trades_today=3), info, 50000)
    assert not r.approved and any("max trades" in x for x in r.reasons)


def test_daily_loss_limit_blocks_new_entries(rm, info, settings):
    limit = -settings.risk.capital_usdt * settings.risk.daily_loss_limit_pct / 100  # -2 USDT
    assert rm.evaluate(make_decision("BUY"), state(todays_pnl=limit + 0.01), info, 50000).approved
    assert not rm.evaluate(make_decision("BUY"), state(todays_pnl=limit), info, 50000).approved
    assert not rm.evaluate(make_decision("BUY"), state(daily_halted=True), info, 50000).approved


def test_daily_loss_does_not_block_closing(rm, info):
    r = rm.evaluate(make_decision("SELL"), state(has_open_position=True, daily_halted=True, todays_pnl=-50), info, 50000)
    assert r.approved and r.action == "CLOSE"


def test_drawdown_blocks_everything(rm, info):
    r = rm.evaluate(make_decision("BUY"), state(equity=89.9, peak_equity=100), info, 50000)
    assert not r.approved and "drawdown" in r.reasons[0]


def test_kill_switch_blocks_everything(rm, info):
    assert not rm.evaluate(make_decision("BUY"), state(kill_switch=True), info, 50000).approved
    assert not rm.evaluate(make_decision("SELL"), state(kill_switch=True, has_open_position=True), info, 50000).approved


def test_sell_without_position_is_not_a_short(rm, info):
    r = rm.evaluate(make_decision("SELL"), state(), info, 50000)
    assert not r.approved and "no shorting" in r.reasons[0]


def test_insufficient_balance(rm, info):
    r = rm.evaluate(make_decision("BUY"), state(quote_available=10.0), info, 50000)  # needs 10 + fee
    assert not r.approved and any("insufficient" in x for x in r.reasons)


def test_limit_helpers(settings):
    assert daily_loss_hit(-2.0, settings.risk) and not daily_loss_hit(-1.99, settings.risk)
    assert drawdown_pct(90, 100) == pytest.approx(10)
    assert drawdown_pct(110, 100) == 0
    assert drawdown_hit(90, 100, settings.risk) and not drawdown_hit(90.1, 100, settings.risk)


# ---- stop-loss / take-profit selection
def test_sl_tp_within_bounds_used(settings):
    assert resolve_sl_tp(1.0, 3.0, settings.risk)[:2] == (1.0, 3.0)


@pytest.mark.parametrize("sl,tp,exp", [
    (0.1, 3.0, (2.0, 3.0)),     # SL below min -> default SL, keep valid TP
    (9.0, 6.0, (2.0, 6.0)),     # SL above max
    (1.0, 0.5, (1.0, 4.0)),     # TP below min
    (1.0, 50.0, (1.0, 4.0)),    # TP above max
    (None, None, (2.0, 4.0)),
    (4.0, 2.0, (2.0, 4.0)),     # reward < risk -> both defaults
])
def test_sl_tp_out_of_bounds_falls_back(settings, sl, tp, exp):
    got_sl, got_tp, notes = resolve_sl_tp(sl, tp, settings.risk)
    assert (got_sl, got_tp) == exp and notes


def test_config_rejects_inconsistent_risk():
    from pydantic import ValidationError
    from config.settings import RiskSettings
    with pytest.raises(ValidationError):
        RiskSettings(default_stop_loss_pct=6.0)  # outside max 5
    with pytest.raises(ValidationError):
        RiskSettings(max_open_positions_per_symbol=2)
    with pytest.raises(ValidationError):
        RiskSettings(position_size_usdt=500, capital_usdt=100)
