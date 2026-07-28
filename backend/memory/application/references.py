"""Stable, non-authoritative references for causally tracing learned context."""
from __future__ import annotations

import copy
from collections.abc import Iterable


def experience_ref(record_id: str) -> str:
    if not record_id.startswith("exp:") or any(ch.isspace() for ch in record_id):
        raise ValueError("invalid experience reference")
    return record_id


def skill_ref(skill_id: str, version: int) -> str:
    if not skill_id.startswith("skill:") or version < 1:
        raise ValueError("invalid skill reference")
    return f"{skill_id}@v{version}"


def validate_tool_refs(
    raw_refs: object, allowed_refs: Iterable[str]
) -> tuple[str, ...]:
    if raw_refs is None:
        return ()
    if not isinstance(raw_refs, list) or any(
        not isinstance(item, str) for item in raw_refs
    ):
        raise ValueError("_memory_refs must be an array of strings")
    allowed = set(allowed_refs)
    refs = tuple(dict.fromkeys(raw_refs))
    unknown = [ref for ref in refs if ref not in allowed]
    if unknown:
        raise ValueError(
            "tool call contains memory refs not injected into this run: "
            + ", ".join(unknown)
        )
    return refs


def add_tool_ref_parameter(
    schemas: list[dict], allowed_refs: Iterable[str]
) -> list[dict]:
    """Add a reserved provenance field without mutating cached tool schemas."""
    refs = tuple(allowed_refs)
    if not refs:
        return schemas
    augmented = copy.deepcopy(schemas)
    for schema in augmented:
        parameters = schema.get("function", {}).setdefault(
            "parameters", {"type": "object", "properties": {}}
        )
        properties = parameters.setdefault("properties", {})
        properties["_memory_refs"] = {
            "type": "array",
            "items": {"type": "string", "enum": list(refs)},
            "description": (
                "Exact injected memory_ref values that materially informed "
                "this tool call. Omit when none were used."
            ),
        }
    return augmented
