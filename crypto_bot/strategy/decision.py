"""The decision format Claude (or the rule-based stand-in) must return, and its parser.

Anything that does not parse, or parses with out-of-range values, becomes a
HOLD with confidence 0 - a bad reply can never cause a trade.
"""
from __future__ import annotations

import json
import math
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator


class Decision(BaseModel):
    model_config = {"extra": "forbid"}

    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=600)
    suggested_stop_loss_pct: float = Field(gt=0, lt=100)
    suggested_take_profit_pct: float = Field(gt=0, le=1000)

    @field_validator("confidence", "suggested_stop_loss_pct", "suggested_take_profit_pct")
    @classmethod
    def _finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("must be a finite number")
        return v

    @classmethod
    def hold(cls, reason: str) -> "Decision":
        return cls(action="HOLD", confidence=0.0, reason=reason[:600] or "hold",
                   suggested_stop_loss_pct=2.0, suggested_take_profit_pct=4.0)


# JSON schema sent to the API for structured output. Numeric ranges are
# enforced by pydantic afterwards rather than in the schema.
DECISION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "suggested_stop_loss_pct": {"type": "number"},
        "suggested_take_profit_pct": {"type": "number"},
    },
    "required": ["action", "confidence", "reason", "suggested_stop_loss_pct", "suggested_take_profit_pct"],
    "additionalProperties": False,
}


def _extract_json_object(text: str) -> Optional[str]:
    """Pull the first {...} object out of text (tolerates ```json fences)."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_decision(text: Optional[str]) -> tuple[Decision, Optional[str]]:
    """Return (decision, error). On any problem: (HOLD, error message)."""
    if not text or not text.strip():
        return Decision.hold("empty reply -> HOLD"), "empty reply"
    blob = _extract_json_object(text)
    if blob is None:
        return Decision.hold("no JSON in reply -> HOLD"), "no JSON object found"
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return Decision.hold("invalid JSON -> HOLD"), f"invalid JSON: {exc}"
    if isinstance(data, dict) and isinstance(data.get("action"), str):
        data["action"] = data["action"].strip().upper()
    try:
        return Decision.model_validate(data), None
    except ValidationError as exc:
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return Decision.hold("reply failed validation -> HOLD"), f"validation failed: {msg}"
