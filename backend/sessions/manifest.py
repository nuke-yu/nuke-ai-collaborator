"""Capability Manifest construction for execution sessions."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ai.providers import resolve_provider_descriptor


MANIFEST_VERSION = 1


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_capability_manifest(
    *,
    provider: str,
    model: str,
    executor_id: str,
    executor_version: str,
    system_prompt: str,
    bot: dict[str, Any],
    tool_schemas: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    permission_rules: Any,
    sandbox_policy: dict[str, Any] | None = None,
    memory_revision: str = "",
) -> tuple[dict[str, Any], str]:
    """Return a bounded manifest and its canonical hash.

    Only hashes and stable identifiers are retained; prompt and skill bodies,
    credentials, and unbounded runtime context never enter the manifest.
    """
    descriptor = resolve_provider_descriptor(provider, model)
    skill_refs = [
        {
            key: item[key]
            for key in ("skill_id", "name", "version", "content_hash")
            if item.get(key) is not None
        }
        for item in skills
        if isinstance(item, dict)
    ]
    rules = getattr(permission_rules, "rules", permission_rules or [])
    permission_refs = [
        {
            "id": getattr(rule, "id", None),
            "tool_pattern": getattr(rule, "tool_pattern", ""),
            "args_pattern": getattr(rule, "args_pattern", ""),
            "action": getattr(rule, "action", ""),
        }
        for rule in rules
    ]
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "provider": {
            "provider_id": descriptor.provider_id,
            "model_id": descriptor.model_id,
            "context_window": descriptor.context_window,
            "max_output_tokens": descriptor.max_output_tokens,
            "supports_tools": descriptor.supports_tools,
            "supports_vision": descriptor.supports_vision,
            "supports_thinking": descriptor.supports_thinking,
            "pricing_version": descriptor.pricing_version,
        },
        "executor": {"id": executor_id, "version": executor_version},
        "prompt_hash": _hash(system_prompt),
        "traits_hash": _hash(bot.get("traits_json", "[]")),
        "skills": sorted(skill_refs, key=lambda item: json.dumps(item, sort_keys=True)),
        "tool_schema_hash": _hash(tool_schemas),
        "permissions_hash": _hash(permission_refs),
        "sandbox_policy": dict(sandbox_policy or {}),
        "memory_revision": str(memory_revision or ""),
    }
    return manifest, _hash(manifest)
