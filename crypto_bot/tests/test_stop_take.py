"""Stop-loss / take-profit price levels and exit detection."""
import pytest

from risk.manager import check_exit, stop_take_prices


def test_levels_from_entry():
    sl, tp = stop_take_prices(100.0, 2.0, 4.0)
    assert sl == pytest.approx(98.0) and tp == pytest.approx(104.0)


@pytest.mark.parametrize("price,exp", [(99.0, None), (98.0, "stop_loss"), (97.0, "stop_loss"),
                                       (103.99, None), (104.0, "take_profit"), (110.0, "take_profit")])
def test_exit_on_current_price(price, exp):
    hit = check_exit(98.0, 104.0, price=price)
    assert (hit[0] if hit else None) == exp


def test_candle_touching_both_assumes_stop_first():
    assert check_exit(98.0, 104.0, high=105, low=97) == ("stop_loss", 98.0)


def test_candle_high_low():
    assert check_exit(98.0, 104.0, high=104.5, low=99) == ("take_profit", 104.0)
    assert check_exit(98.0, 104.0, high=103, low=99) is None


def test_no_data_no_exit():
    assert check_exit(98.0, 104.0) is None
