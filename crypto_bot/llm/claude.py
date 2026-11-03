"""Claude as a decision engine, via the official ``anthropic`` Python SDK.

Safety properties:
  * Every failure path (network error, bad key, refusal, truncated or invalid
    JSON, cost cap reached) returns HOLD - never a trade.
  * Token usage and estimated cost are stored per UTC day in SQLite; once the
    configured daily cap is reached the bot stops calling the API until the
    next UTC day.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import anthropic

from config.settings import LLMSettings
from llm.prompts import SYSTEM_PROMPT, build_user_prompt
from storage.db import Store, day_of
from strategy.decision import DECISION_JSON_SCHEMA, Decision, parse_decision
from strategy.snapshot import MarketSnapshot

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class CostTracker:
    """Estimates API spend from the token counts the API reports back."""

    def __init__(self, cfg: LLMSettings, store: Store) -> None:
        self.cfg = cfg
        self.store = store

    def cost_of(self, usage: Any) -> tuple[int, int, float]:
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)  # includes reasoning tokens
        cost = (
            (inp + cache_write * 1.25 + cache_read * 0.1) * self.cfg.input_price_per_mtok
            + out * self.cfg.output_price_per_mtok
        ) / 1_000_000
        return inp + cache_write + cache_read, out, cost

    def record(self, usage: Any) -> float:
        inp, out, cost = self.cost_of(usage)
        self.store.add_llm_usage(day_of(), inp, out, cost)
        return cost

    def spent_today(self) -> float:
        return float(self.store.llm_usage(day_of())["cost_usd"])

    def cap_reached(self) -> bool:
        return self.spent_today() >= self.cfg.daily_cost_cap_usd


class ClaudeEngine:
    name = "claude"

    def __init__(self, cfg: LLMSettings, api_key: Optional[str], store: Store,
                 client: Optional[anthropic.Anthropic] = None) -> None:
        self.cfg = cfg
        self.costs = CostTracker(cfg, store)
        if client is None:
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY is not set (put it in .env), or use llm.engine: rules")
            # The SDK itself retries 429/5xx/connection errors with backoff.
            client = anthropic.Anthropic(api_key=api_key, timeout=cfg.timeout_seconds, max_retries=3)
        self.client = client
        self._use_fallback = cfg.refusal_fallback
        self._use_structured = cfg.structured_output
        # Exposed so the trader can log exactly what Claude said.
        self.last_raw: Optional[str] = None
        self.last_error: Optional[str] = None

    def _request(self, user_prompt: str) -> Any:
        output_config: dict[str, Any] = {"effort": self.cfg.effort}
        if self._use_structured:
            output_config["format"] = {"type": "json_schema", "schema": DECISION_JSON_SCHEMA}
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config=output_config,
        )
        if self._use_fallback:
            return self.client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
        return self.client.messages.create(**kwargs)

    def decide(self, snap: MarketSnapshot) -> Decision:
        self.last_raw, self.last_error = None, None
        if self.costs.cap_reached():
            self.last_error = f"daily LLM cost cap ${self.cfg.daily_cost_cap_usd:.2f} reached"
            return Decision.hold(self.last_error)

        prompt = build_user_prompt(snap)
        try:
            try:
                resp = self._request(prompt)
            except anthropic.BadRequestError as exc:
                if not (self._use_fallback or self._use_structured):
                    raise
                # An optional feature was rejected (e.g. model/beta mismatch).
                # Turn optional features off for this session and retry once.
                log.warning("Claude rejected optional request features (%s); retrying without them", exc)
                self._use_fallback = self._use_structured = False
                resp = self._request(prompt)
        except anthropic.AuthenticationError:
            self.last_error = "Anthropic authentication failed - check ANTHROPIC_API_KEY"
            log.error(self.last_error)
            return Decision.hold(self.last_error)
        except anthropic.RateLimitError as exc:
            self.last_error = f"Anthropic rate limit: {exc}"
            log.warning(self.last_error)
            return Decision.hold("rate limited")
        except anthropic.APIStatusError as exc:
            self.last_error = f"Anthropic API error {exc.status_code}: {exc}"
            log.error(self.last_error)
            return Decision.hold("API error")
        except anthropic.APIConnectionError as exc:
            self.last_error = f"cannot reach Anthropic API: {exc}"
            log.error(self.last_error)
            return Decision.hold("API unreachable")

        cost = self.costs.record(resp.usage)
        log.info("Claude call: in=%s out=%s cost=$%.5f (today $%.4f / cap $%.2f)",
                 resp.usage.input_tokens, resp.usage.output_tokens, cost, self.costs.spent_today(),
                 self.cfg.daily_cost_cap_usd)

        if resp.stop_reason == "refusal":
            self.last_error = "Claude declined the request (refusal)"
            return Decision.hold(self.last_error)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        self.last_raw = text
        if resp.stop_reason == "max_tokens":
            self.last_error = "reply truncated (max_tokens) - raise llm.max_tokens"
            return Decision.hold(self.last_error)
        decision, err = parse_decision(text)
        if err:
            self.last_error = err
            log.warning("Claude reply rejected -> HOLD: %s | raw=%r", err, text[:300])
        return decision
