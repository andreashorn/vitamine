"""Small, explicit model overrides for individual LLM workloads."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any


def settings_for_llm_task(settings: Mapping[str, Any], task: str) -> dict[str, Any]:
    """Return a copy of *settings* with a configured task model selected.

    The deployment's ``api_model`` remains the default for every workload.
    ``task_models`` only overrides that model for explicitly named OpenAI API
    tasks, so an experiment cannot silently change other LLM-backed features.
    """

    scoped = dict(settings)
    # The hosted service has one managed OpenAI account. A workspace receives
    # this short-lived flag only after the gateway has checked the member's
    # versioned preference; keeping the check here also prevents a newly added
    # LLM-backed workspace feature from silently bypassing that boundary.
    if (
        os.environ.get("VITAMINE_CLOUD_WORKER") == "1"
        and str(scoped.get("provider") or "none") == "openai"
        and os.environ.get("VITAMINE_OPENAI_PROCESSING_CONSENT") != "1"
    ):
        scoped["provider"] = "none"
        return scoped
    task_models = settings.get("task_models")
    if not isinstance(task_models, Mapping):
        # Hosted task routing belongs to deployment policy, not to the user's
        # ordinary CV-import preferences. Import lazily to keep this helper
        # independent of application startup order.
        try:
            from .deployment import llm_policy

            task_models = llm_policy().get("task_models")
        except (OSError, RuntimeError, TypeError, ValueError):
            task_models = None
    if str(settings.get("provider") or "none") not in {"openai", "openai_compatible"}:
        return scoped
    if not isinstance(task_models, Mapping):
        return scoped
    model = str(task_models.get(task) or "").strip()
    if model:
        scoped["api_model"] = model
    return scoped
