"""Claude integration with a fake client: parsing, cost cap, refusals, errors."""
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from llm.claude import ClaudeEngine
from storage.db import Store
from strategy.snapshot import build_snapshot
from tests.conftest import make_candles

GOOD = '{"action":"BUY","confidence":0.8,"reason":"ok","suggested_stop_loss_pct":2,"suggested_take_profit_pct":4}'


def resp(text=GOOD, stop="end_turn", inp=1000, out=200):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, usage=SimpleNamespace(input_tokens=inp, output_tokens=out,
                                                                   cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)
        self.beta = SimpleNamespace(messages=self.messages)


@pytest.fixture
def snap():
    return build_snapshot("BTC/USDT", "15m", make_candles(), 112.0, None, 0.0, 0)


def engine(settings, tmp_path, replies):
    return ClaudeEngine(settings.llm, None, Store(tmp_path / "llm.db"), client=FakeClient(replies))


def test_valid_reply_and_cost_tracking(settings, tmp_path, snap):
    e = engine(settings, tmp_path, [resp()])
    d = e.decide(snap)
    assert d.action == "BUY" and e.last_error is None
    # 1000 in * $2/M + 200 out * $10/M = 0.002 + 0.002
    assert e.costs.spent_today() == pytest.approx(0.004)
    call = e.client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"
    assert "temperature" not in call


def test_prompt_contains_required_fields(settings, tmp_path, snap):
    e = engine(settings, tmp_path, [resp()])
    e.decide(snap)
    prompt = e.client.messages.calls[0]["messages"][0]["content"]
    for key in ("rsi14", "ema20", "ema50", "macd", "atr14", "volume_change_pct", "last_price",
                "todays_pnl_usdt", "position", "recent_ohlcv", "timeframe", "symbol"):
        assert key in prompt


def test_cost_cap_stops_calls(settings, tmp_path, snap):
    settings.llm.daily_cost_cap_usd = 0.005
    e = engine(settings, tmp_path, [resp(), resp()])
    assert e.decide(snap).action == "BUY"     # spent 0.004
    assert e.decide(snap).action == "BUY"     # spent 0.008 (one call may overshoot)
    d = e.decide(snap)                        # cap reached -> no call at all
    assert d.action == "HOLD" and "cap" in e.last_error
    assert len(e.client.messages.calls) == 2


def test_invalid_json_is_hold(settings, tmp_path, snap):
    e = engine(settings, tmp_path, [resp("I think you should buy!")])
    assert e.decide(snap).action == "HOLD" and e.last_error


def test_out_of_range_is_hold(settings, tmp_path, snap):
    e = engine(settings, tmp_path, [resp(GOOD.replace("0.8", "7"))])
    assert e.decide(snap).action == "HOLD"


def test_refusal_and_truncation_are_hold(settings, tmp_path, snap):
    e = engine(settings, tmp_path, [resp("", stop="refusal"), resp('{"action":"BU', stop="max_tokens")])
    assert e.decide(snap).action == "HOLD"
    assert e.decide(snap).action == "HOLD" and "truncated" in e.last_error


def _err(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("err", response=httpx.Response(status, request=req), body=None)


def test_bad_request_retries_without_optional_features(settings, tmp_path, snap):
    e = engine(settings, tmp_path, [_err(anthropic.BadRequestError, 400), resp()])
    assert e.decide(snap).action == "BUY"
    second = e.client.messages.calls[1]
    assert "fallbacks" not in second and "format" not in second["output_config"]


def test_api_errors_are_hold(settings, tmp_path, snap):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    e = engine(settings, tmp_path, [_err(anthropic.AuthenticationError, 401),
                                    _err(anthropic.InternalServerError, 500),
                                    anthropic.APIConnectionError(request=req)])
    for _ in range(3):
        assert e.decide(snap).action == "HOLD"


def test_missing_key_raises(settings, tmp_path):
    with pytest.raises(ValueError):
        ClaudeEngine(settings.llm, None, Store(tmp_path / "x.db"))
