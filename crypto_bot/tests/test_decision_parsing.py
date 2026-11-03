"""Claude's reply must be strict JSON matching the schema; anything else -> HOLD."""
import pytest

from strategy.decision import Decision, parse_decision

GOOD = '{"action": "BUY", "confidence": 0.82, "reason": "trend up", "suggested_stop_loss_pct": 1.5, "suggested_take_profit_pct": 3}'


def test_valid_json_parses():
    d, err = parse_decision(GOOD)
    assert err is None
    assert d.action == "BUY" and d.confidence == 0.82 and d.suggested_take_profit_pct == 3


def test_code_fences_and_surrounding_text_tolerated():
    d, err = parse_decision(f"Here you go:\n```json\n{GOOD}\n```\nThanks")
    assert err is None and d.action == "BUY"


def test_lowercase_action_normalised():
    d, err = parse_decision(GOOD.replace('"BUY"', '"buy"'))
    assert err is None and d.action == "BUY"


def test_braces_inside_reason_string():
    txt = GOOD.replace("trend up", "range {low} } broken")
    d, err = parse_decision(txt)
    assert err is None and "broken" in d.reason


@pytest.mark.parametrize("text", [
    "", "   ", None, "no json here", "{not json}", '{"action": "BUY"',
])
def test_garbage_becomes_hold(text):
    d, err = parse_decision(text)
    assert d.action == "HOLD" and d.confidence == 0.0 and err


@pytest.mark.parametrize("field,value", [
    ("confidence", "1.5"), ("confidence", "-0.1"), ("suggested_stop_loss_pct", "0"),
    ("suggested_stop_loss_pct", "-2"), ("suggested_stop_loss_pct", "150"),
    ("suggested_take_profit_pct", "0"), ("action", '"SHORT"'), ("confidence", "NaN"),
])
def test_out_of_range_values_become_hold(field, value):
    import json
    data = json.loads(GOOD)
    data[field] = json.loads(value) if value != "NaN" else float("nan")
    d, err = parse_decision(json.dumps(data))
    assert d.action == "HOLD" and err and "validation" in err


def test_missing_and_extra_keys_rejected():
    import json
    data = json.loads(GOOD)
    del data["reason"]
    assert parse_decision(json.dumps(data))[0].action == "HOLD"
    data = json.loads(GOOD)
    data["leverage"] = 10
    assert parse_decision(json.dumps(data))[0].action == "HOLD"


def test_hold_factory_is_safe():
    d = Decision.hold("x")
    assert d.action == "HOLD" and d.confidence == 0
