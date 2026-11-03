"""Fixed-USDT sizing respecting lot size, minimum amount and minimum order value."""
import pytest

from exchange.base import MarketInfo
from risk.manager import compute_order_size


def test_amount_rounded_down_to_lot(info):
    r = compute_order_size(10, 33333.33, info, 100)
    assert r.ok
    assert r.amount == pytest.approx(0.0003)   # 10/33333.33 = 0.00030000003 -> 0.00030
    assert r.amount * 33333.33 <= 10 + 1e-9    # never more than the configured size


def test_round_down_never_up():
    mi = MarketInfo("X/USDT", "X", "USDT", amount_step=0.01)
    assert mi.round_amount_down(1.239999) == pytest.approx(1.23)
    assert mi.round_amount_down(1.24) == pytest.approx(1.24)
    assert mi.round_amount_down(0.009) == 0
    mi = MarketInfo("X/USDT", "X", "USDT", amount_step=1)
    assert mi.round_amount_down(7.9) == 7


def test_below_min_cost(info):
    r = compute_order_size(4, 50000, info, 100)  # min_cost 5
    assert not r.ok and "minimum" in r.reason


def test_below_min_amount():
    mi = MarketInfo("X/USDT", "X", "USDT", amount_step=0.1, min_amount=1.0, min_cost=None)
    r = compute_order_size(10, 20, mi, 100)  # 0.5 units < 1.0
    assert not r.ok and "below exchange minimum" in r.reason


def test_rounds_to_zero():
    mi = MarketInfo("X/USDT", "X", "USDT", amount_step=1.0)
    r = compute_order_size(10, 50, mi, 100)
    assert not r.ok and "zero" in r.reason


def test_fee_included_in_balance_check(info):
    assert not compute_order_size(10, 50000, info, 10.0).ok
    assert compute_order_size(10, 50000, info, 10.01).ok


def test_invalid_price(info):
    assert not compute_order_size(10, 0, info, 100).ok
