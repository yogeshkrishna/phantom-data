"""Optional Claude fallback for request-intent classification (off by default).

Enabled only with `python code/main.py --llm` *and* credentials the Anthropic SDK can resolve
(ANTHROPIC_API_KEY or an `ant auth login` profile). It is consulted only when the deterministic
classifier is not confident, and every call is recorded with its token usage so the usage
report can be generated from real telemetry.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .authorization import AuthorizationDecision

MODEL = "claude-opus-5-5"
# Populate with dated verified prices before using paid runs. Unknown returned
# model IDs must never inherit another model's price.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {}
PROMPTS = Path(__file__).resolve().parent / "prompts.md"

SCHEMA = {
    "type": "object",
    "properties": {
        "authorizes_deletion": {"type": "boolean"},
        "intent": {"type": "string", "enum": ["deletion", "access", "portability", "correction",
                                              "marketing_opt_out", "account_support", "other"]},
        "reason": {"type": "string"},
    },
    "required": ["authorizes_deletion", "intent", "reason"],
    "additionalProperties": False,
}


def load_prompt(name: str) -> str:
    text = PROMPTS.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(name)}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        raise KeyError(name)
    return m.group(1).strip()


class ClaudeFallback:
    def __init__(self, client):
        self.client = client
        self.system = load_prompt("authorization_system")
        self.usage: list[dict] = []

    @classmethod
    def from_env(cls):
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE")):
            return None
        try:
            import anthropic
        except ImportError:
            return None
        return cls(anthropic.Anthropic())

    def classify(self, text: str, fallback: AuthorizationDecision) -> AuthorizationDecision:
        import anthropic

        try:
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=2048,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=self.system,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": f"<request>\n{text}\n</request>"}],
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            self.usage.append({"model": MODEL, "error": type(exc).__name__, "input_tokens": 0, "output_tokens": 0})
            return fallback
        self.usage.append({
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "stop_reason": response.stop_reason,
            "request_id": getattr(response, "_request_id", None),
        })
        if response.stop_reason != "end_turn":
            return fallback  # refusal / truncation: keep the deterministic decision
        payload = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return fallback
        if (not isinstance(data, dict) or set(data) != {"authorizes_deletion", "intent", "reason"}
                or type(data["authorizes_deletion"]) is not bool
                or data["intent"] not in SCHEMA["properties"]["intent"]["enum"]
                or not isinstance(data["reason"], str)):
            return fallback
        return AuthorizationDecision(data["authorizes_deletion"], f"llm:{data['intent']}",
                                     [data.get("reason", "")], confident=True)


def estimate_cost(usage: list[dict]) -> dict:
    totals: dict[str, dict] = {}
    for u in usage:
        t = totals.setdefault(u["model"], {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        t["calls"] += 1
        t["input_tokens"] += u.get("input_tokens", 0)
        t["output_tokens"] += u.get("output_tokens", 0)
    for model, t in totals.items():
        price = PRICE_PER_MTOK.get(model)
        t["cost_usd"] = None if price is None else (t["input_tokens"] * price[0] + t["output_tokens"] * price[1]) / 1e6
    return totals
