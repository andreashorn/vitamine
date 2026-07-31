"""Privacy-minimal capture of provider-reported LLM token usage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_MODEL_RE = re.compile(r"[^A-Za-z0-9._:/-]+")


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
