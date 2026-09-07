"""Deployment-specific VitaMine capabilities.

The desktop and hosted applications deliberately share the same code. This
module is the single boundary for behavior that differs between deployments.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "vitamine-desktop.json"


@lru_cache(maxsize=1)
def deployment_config() -> dict[str, Any]:
    path = Path(os.environ.get("VITAMINE_DEPLOYMENT_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load VitaMine deployment configuration at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"VitaMine deployment configuration must be a JSON object: {path}")
    llm = raw.get("llm")
    onboarding = raw.get("onboarding")
    if not isinstance(llm, dict) or not isinstance(onboarding, dict):
        raise RuntimeError(f"VitaMine deployment configuration is missing llm/onboarding sections: {path}")
    return raw


def llm_policy() -> dict[str, Any]:
    return deployment_config()["llm"]


def managed_llm() -> bool:
    return bool(llm_policy().get("managed"))


def llm_user_configuration_allowed() -> bool:
    return bool(llm_policy().get("allow_user_configuration", True))


def skip_llm_onboarding() -> bool:
    return bool(deployment_config()["onboarding"].get("skip_llm_configuration"))
