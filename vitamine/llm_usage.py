"""Privacy-minimal capture of provider-reported LLM token usage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


SAFE_MODEL_RE = re.compile(r"[^A-Za-z0-9._:/-]+")
PRICING_VERSION = "openai-standard-2026-07-31"
PREMIUM_MARKUP_BASIS_POINTS = 20_000
MICRO_USD_PER_USD = 1_000_000
MODEL_PRICES_PER_MILLION = {
    "gpt-4.1": (Decimal("2.00"), Decimal("0.50"), Decimal("8.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("0.10"), Decimal("1.60")),
    "gpt-4.1-nano": (Decimal("0.10"), Decimal("0.025"), Decimal("0.40")),
    "gpt-4o": (Decimal("2.50"), Decimal("1.25"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.075"), Decimal("0.60")),
}


def canonical_priced_model(model: str) -> str | None:
    value = str(model or "").lower()
    for candidate in sorted(MODEL_PRICES_PER_MILLION, key=len, reverse=True):
        if value == candidate or value.startswith(candidate + "-"):
            return candidate
    return None


def usage_costs(event: dict[str, Any]) -> dict[str, Any]:
    priced_model = canonical_priced_model(str(event.get("model") or ""))
    if not priced_model:
        return {"priced_model": None, "pricing_version": PRICING_VERSION, "wholesale_cost_microusd": None,
                "charged_cost_microusd": None, "markup_basis_points": PREMIUM_MARKUP_BASIS_POINTS}
    input_rate, cached_rate, output_rate = MODEL_PRICES_PER_MILLION[priced_model]
    input_tokens = int(event.get("input_tokens") or 0)
    cached_tokens = min(input_tokens, int(event.get("cached_input_tokens") or 0))
    output_tokens = int(event.get("output_tokens") or 0)
    wholesale = (
        Decimal(input_tokens - cached_tokens) * input_rate
        + Decimal(cached_tokens) * cached_rate
        + Decimal(output_tokens) * output_rate
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    wholesale_microusd = int(wholesale)
    charged_microusd = int(
        (Decimal(wholesale_microusd) * Decimal(PREMIUM_MARKUP_BASIS_POINTS) / Decimal(10_000))
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return {
        "priced_model": priced_model,
        "pricing_version": PRICING_VERSION,
        "wholesale_cost_microusd": wholesale_microusd,
        "charged_cost_microusd": charged_microusd,
        "markup_basis_points": PREMIUM_MARKUP_BASIS_POINTS,
    }


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def usage_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    response_id = str(payload.get("id") or "").strip()
    event_key = (
        "openai:" + hashlib.sha256(response_id.encode("utf-8")).hexdigest()
        if response_id
        else "local:" + secrets.token_hex(16)
    )
    model = SAFE_MODEL_RE.sub("", str(payload.get("model") or "unknown"))[:120] or "unknown"
    return {
        "event_key": event_key,
        "provider": "openai",
        "model": model,
        "input_tokens": _count(usage.get("prompt_tokens", usage.get("input_tokens"))),
        "cached_input_tokens": _count(input_details.get("cached_tokens")) if isinstance(input_details, dict) else None,
        "output_tokens": _count(usage.get("completion_tokens", usage.get("output_tokens"))),
        "reasoning_tokens": _count(output_details.get("reasoning_tokens")) if isinstance(output_details, dict) else None,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def record_usage(payload: dict[str, Any]) -> None:
    path_value = os.environ.get("VITAMINE_LLM_USAGE_PATH", "").strip()
    event = usage_event(payload)
    if not path_value or event is None:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
