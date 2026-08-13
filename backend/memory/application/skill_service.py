"""Declarative Skill compilation, recall, promotion, and projection services."""
from __future__ import annotations

import json
import re
from typing import Any

from memory.application import CanonicalLearningService, CanonicalSkillProjectionService
from memory.contracts import (
    CompleteSkillUsage, ListSkillCandidates, RecallSkills, ResolveLearningRefs,
)
from memory.domain import MemoryScope, UsageKind
from memory.infrastructure import SQLiteMemoryDatabase

_SAFE_TOOL = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,79}$")
_BANNED = {"run_shell", "bash", "shell", "eval", "exec"}


def _bounded_snapshot(value: Any, depth: int = 0) -> Any:
    if depth >= 8:
        return "[nested payload truncated]"
    if isinstance(value, dict):
        return {str(k): _bounded_snapshot(v, depth + 1) for k, v in list(value.items())[:512]}
    if isinstance(value, (list, tuple)):
        return [_bounded_snapshot(v, depth + 1) for v in list(value)[:512]]
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def validate_declaration(value: dict) -> None:
    risk = value.get("risk_level")
    if risk not in {"S0", "S1"}:
        raise ValueError("only declarative S0/S1 skills may be compiled")
    if not value.get("trigger") or not value.get("procedure"):
        raise ValueError("skill requires trigger and procedure")
    tools = value.get("allowed_tools") or []
    if risk == "S0" and tools:
        raise ValueError("S0 skills cannot call tools")
    if any(not _SAFE_TOOL.match(tool) or tool in _BANNED for tool in tools):
        raise ValueError("unsafe or executable tool in learned skill")
    if {"code", "python", "shell", "shell_command", "executable"}.intersection(value):
        raise ValueError("executable code fields are forbidden in learned skills")
    procedure = value.get("procedure") if isinstance(value.get("procedure"), list) else [value.get("procedure")]
    text = " ".join(str(item) for item in procedure).lower()
    if any(marker in text for marker in ("os.system(", "subprocess.", "eval(", "exec(", "curl |", "bash -c")):
        raise ValueError("executable instructions are forbidden in learned skills")
    encoded = json.dumps(value, ensure_ascii=False).lower()
    if "bypasspermissions" in encoded or "bypass_permissions" in encoded:
        raise ValueError("permission bypass is forbidden")


def _bot_scope(group_id: int, bot_id: int, run_id: str | None = None) -> MemoryScope:
    return MemoryScope.bot(group_id=group_id, bot_id=bot_id, actor_id=f"bot:{bot_id}", run_id=run_id)


async def compile_candidate(record_id: str, group_id: int) -> str | None:
    from memory.canonical import build_skill_compiler
    result = await build_skill_compiler().compile(group_id, record_id)
    if result.get("skill_id"):
        from memory.canonical import _runtime_composition
        database = _runtime_composition().database
        async with await database.connect("pipeline_jobs", group_id, write=True) as db:
            await db.execute(
                """UPDATE pipeline_jobs SET status='completed',output_json=?,completed_at=updated_at
                   WHERE group_id=? AND job_type='compile_skill_candidate' AND input_id=? AND status='pending'""",
                ('{"source":"canonical_skill_compilation"}', group_id, record_id),
            )
            await db.commit()
    return result.get("skill_id")


async def list_everos_source_documents(*, group_id: int, record_id: str | None = None,
                                       limit: int = 100) -> list[dict[str, object]]:
    from memory.canonical import _runtime_composition
    database = _runtime_composition().database
    async with await database.connect("skills", group_id, write=False) as db:
        query = "SELECT source_id,record_id,source_type,content_json,created_at FROM everos_source_documents WHERE group_id=?"
        params: list[object] = [group_id]
        if record_id is not None:
            query += " AND record_id=?"
            params.append(record_id)
        query += " ORDER BY created_at DESC,source_id DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        try:
            async with db.execute(query, tuple(params)) as cur:
                rows = await cur.fetchall()
        except Exception:
            return []
    return [{"source_id": str(row[0]), "record_id": str(row[1]), "source_type": str(row[2]),
             "content_json": str(row[3]), "created_at": int(row[4])} for row in rows]


async def get_everos_source_markdown(*, group_id: int, record_id: str) -> str | None:
    from memory.canonical import _runtime_composition
    async with await _runtime_composition().database.connect("skills", group_id, write=False) as db:
        try:
            async with db.execute(
                "SELECT markdown FROM everos_source_markdown WHERE group_id=? AND record_id=? ORDER BY created_at DESC LIMIT 1",
                (group_id, record_id),
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            return None
    return None if not row else str(row[0])


async def list_skill_candidates(*, group_id: int, bot_id: int) -> list[dict]:
    from memory.canonical import build_learning_client
    candidates = await build_learning_client().list_skill_candidates(
        ListSkillCandidates(scope=_bot_scope(group_id, bot_id))
    )
    return [{"skill_id": item.skill_id, "name": item.name, "maturity": item.maturity,
             "risk_level": item.risk_level, "version": item.version,
             "success_count": item.success_count, "failure_count": item.failure_count,
             "declaration": dict(item.declaration), "evidence_ids": item.evidence_ids}
            for item in candidates if item.maturity == "trial"]


async def promote_skill(skill_id: str, group_id: int, target_maturity: str = "active",
                        *, bot_id: int | None = None, actor_id: str, reason: str) -> bool:
    from memory.canonical import build_learning_client
    return await build_learning_client().promote_skill(
        skill_id=skill_id, group_id=group_id, target_maturity=target_maturity,
        bot_id=bot_id, actor_id=actor_id, reason=reason,
    )


async def recall_skills(*, query: str, run_id: str, group_id: int | None,
                        bot_id: int | None, limit: int = 2) -> tuple[str, list[str]]:
    if group_id is None or bot_id is None:
        return "", []
    from memory.canonical import build_learning_client
    return await build_learning_client().recall_skills(
        RecallSkills(scope=_bot_scope(group_id, bot_id, run_id), query=query,
                     run_id=run_id, limit=limit)
    )


async def resolve_skill_refs(*, skill_ids: list[str], group_id: int, bot_id: int) -> tuple[str, ...]:
    from memory.canonical import build_learning_client
    return await build_learning_client().resolve_learning_refs(ResolveLearningRefs(
        scope=_bot_scope(group_id, bot_id), skill_ids=tuple(skill_ids)
    ))


async def complete_skill_usage(*, skill_ids: list[str], run_id: str,
                               group_id: int | None, outcome: str) -> None:
    if group_id is None:
        return
    from memory.canonical import build_learning_client
    await build_learning_client().record_completion_telemetry(type("Completion", (), {
        "scope": MemoryScope.group(group_id=group_id, actor_id="service:skill_usage"),
        "kind": UsageKind.SKILL, "item_ids": tuple(skill_ids), "run_id": run_id,
        "outcome": outcome, "input_tokens": 0, "output_tokens": 0,
        "tool_attempts": 0,
    })())


async def project_skill(skill_id: str, group_id: int) -> str | None:
    from memory.canonical import build_skill_projection_client
    return await build_skill_projection_client().project(skill_id, group_id)


async def enqueue_missing_skill_projections(group_id: int) -> int:
    from memory.canonical import build_learning_client
    return await build_learning_client().repair_skill_projection_gaps(group_id)


__all__ = ["_bounded_snapshot", "validate_declaration", "compile_candidate",
           "list_everos_source_documents", "get_everos_source_markdown",
           "list_skill_candidates", "promote_skill", "recall_skills",
           "resolve_skill_refs", "complete_skill_usage", "project_skill",
           "enqueue_missing_skill_projections"]
