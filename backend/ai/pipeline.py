"""Durable and idempotent Memory & Learning post-processing jobs."""
from __future__ import annotations
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ai.cases import evaluate_outcome

from memory.adapters.runtime.learning_legacy import LegacyPipelineJobAdapter
from memory.contracts import LostLeaseError
from memory.domain import MemoryScope

log = logging.getLogger(__name__)

_pipeline_repo = LegacyPipelineJobAdapter()
_JobHandler = Callable[[int, str, str], Awaitable[dict]]


async def enqueue(*, job_type: str, group_id: int, input_id: str,
                  input_version: str = "1") -> str:
    scope = MemoryScope.group(group_id=group_id, actor_id="pipeline")
    return await _pipeline_repo.enqueue(scope, job_type=job_type, input_id=input_id, input_version=input_version)


async def process_case(case_id: str, group_id: int, *, input_version: str = "1") -> str:
    """Durably enqueue Case learning; the owning Worker dispatches it."""
    return await enqueue(
        job_type="evaluate_case",
        group_id=group_id,
        input_id=case_id,
        input_version=input_version,
    )


async def enqueue_turn_observation(
    *, message_id: int, bot_id: int, group_id: int, input_version: str = "1"
) -> str:
    """Persist the post-turn capture boundary using stable message identity."""
    return await enqueue(
        job_type="observe_turn",
        group_id=group_id,
        input_id=f"{message_id}:{bot_id}",
        input_version=input_version,
    )


def _parse_observation_input(input_id: str) -> tuple[int, int]:
    try:
        message_raw, bot_raw = input_id.split(":", 1)
        message_id, bot_id = int(message_raw), int(bot_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid observe_turn input: {input_id}") from exc
    if message_id <= 0 or bot_id <= 0:
        raise ValueError(f"invalid observe_turn input: {input_id}")
    return message_id, bot_id


async def _load_observation_event(group_id: int, input_id: str):
    from ai.memory import _memory_db
    from ai.memory_provider import MemoryEvent, get_memory_provider
    import db as database

    try:
        message_id, bot_id = _parse_observation_input(input_id)
    except ValueError:
        return None
    async with await _memory_db("messages", group_id, write=False) as conn:
        async with conn.execute(
            """SELECT content,sender_name,sender_provider,sender_model,meta,is_deleted
               FROM messages WHERE id=? AND group_id=? AND member_id=?""",
            (message_id, group_id, bot_id),
        ) as cur:
            row = await cur.fetchone()
    if not row or row[5]:
        return None

    bot = None
    async with database.global_db() as central:
        bot = await database.get_member(central, bot_id)
    if bot is None:
        return None
    if int(bot.get("group_id") or 0) != group_id:
        raise ValueError(f"observation bot {bot_id} is outside group {group_id}")
    provider = get_memory_provider(bot)
    if not getattr(provider, "durable_observation_enabled", True):
        return None

    try:
        metadata = json.loads(row[4] or "{}")
    except (TypeError, ValueError):
        metadata = {}
    observation_meta = metadata.get("memory_observation") or {}
    event = MemoryEvent(
        bot_id=bot_id,
        group_id=group_id,
        role=str((bot or {}).get("role") or ""),
        bot_name=str((bot or {}).get("name") or row[1] or ""),
        message_id=message_id,
        text=str(row[0]),
        provider=str(row[2] or (bot or {}).get("model_provider") or ""),
        model=str(row[3] or (bot or {}).get("model_name") or ""),
        thread_id=observation_meta.get("thread_id") or None,
    )
    return event


async def _observe_turn(group_id: int, input_id: str, input_version: str) -> dict:
    """Fan one capture boundary into independently leased durable stages."""
    child_types = (
        "observe_turn_fact",
        "observe_turn_summary",
        "observe_turn_reflection",
        "observe_turn_tool_compression",
    )
    child_ids = [
        await enqueue(
            job_type=job_type,
            group_id=group_id,
            input_id=input_id,
            input_version=input_version,
        )
        for job_type in child_types
    ]
    return {"child_job_ids": child_ids}


async def _run_observation_stage(
    group_id: int, input_id: str, stage: str
) -> dict[str, Any]:
    event = await _load_observation_event(group_id, input_id)
    if event is None:
        return {"stage": stage, "skipped": True}
    compact_role = event.role or event.bot_name
    if stage == "fact":
        from ai.memory import add_to_chroma
        await add_to_chroma(
            event.message_id, event.text, event.role, event.bot_id,
            event.group_id, event.provider, event.model, event.thread_id,
            strict=True,
        )
    elif stage == "summary":
        from ai.memory import maybe_summarize
        await maybe_summarize(
            event.group_id, event.bot_id, compact_role,
            [event.bot_id], event.thread_id, strict=True,
        )
    elif stage == "reflection":
        from ai.memory import maybe_reflect
        await maybe_reflect(
            event.group_id, event.bot_id, compact_role,
            event.provider, event.model, strict=True,
        )
    elif stage == "tool_compression":
        from ai.tool_events import maybe_compress_tool_events
        await maybe_compress_tool_events(
            event.group_id, event.bot_id, compact_role,
            event.thread_id, event.provider, event.model, strict=True,
        )
    else:
        raise ValueError(f"unsupported observation stage: {stage}")
    return {"stage": stage, "skipped": False}


async def _observe_turn_fact(group_id: int, input_id: str, _version: str) -> dict:
    return await _run_observation_stage(group_id, input_id, "fact")


async def _observe_turn_summary(group_id: int, input_id: str, _version: str) -> dict:
    return await _run_observation_stage(group_id, input_id, "summary")


async def _observe_turn_reflection(group_id: int, input_id: str, _version: str) -> dict:
    return await _run_observation_stage(group_id, input_id, "reflection")


async def _observe_turn_tool_compression(
    group_id: int, input_id: str, _version: str
) -> dict:
    return await _run_observation_stage(group_id, input_id, "tool_compression")


async def enqueue_missing_turn_observations(
    group_id: int, *, limit: int = 100
) -> int:
    """Repair the message-commit → enqueue gap from a durable high-water mark."""
    from ai.memory import _memory_db
    import db as database
    import time

    async with database.global_db() as central:
        members = await database.get_members(central, group_id)
    bot_ids = sorted(
        int(member["id"]) for member in members if member.get("type") == "bot"
    )
    if not bot_ids:
        return 0
    now = int(time.time() * 1000)
    async with await _memory_db(
        "memory_observation_scan_state", group_id, write=True
    ) as conn:
        await conn.execute(
            """INSERT OR IGNORE INTO memory_observation_scan_state
               (group_id,scan_after_message_id,updated_at) VALUES(?,0,?)""",
            (group_id, now),
        )
        async with conn.execute(
            """SELECT scan_after_message_id FROM memory_observation_scan_state
               WHERE group_id=?""",
            (group_id,),
        ) as cur:
            cursor = int((await cur.fetchone())[0])
        await conn.commit()
    placeholders = ",".join("?" for _ in bot_ids)
    async with await _memory_db("messages", group_id, write=False) as conn:
        async with conn.execute(
            f"""SELECT m.id,m.member_id,p.job_id
                FROM messages m
                LEFT JOIN pipeline_jobs p
                  ON p.group_id=m.group_id AND p.job_type='observe_turn'
                 AND p.input_id=CAST(m.id AS TEXT)||':'||CAST(m.member_id AS TEXT)
                 AND p.input_version='1'
                WHERE m.group_id=? AND m.member_id IN ({placeholders})
                  AND m.id>? AND m.is_deleted=0
                ORDER BY m.id LIMIT ?""",
            (group_id, *bot_ids, cursor, max(1, limit)),
        ) as cur:
            rows = await cur.fetchall()
    missing = [
        (message_id, bot_id)
        for message_id, bot_id, job_id in rows
        if job_id is None
    ]
    for message_id, bot_id in missing:
        await enqueue_turn_observation(
            message_id=int(message_id), bot_id=int(bot_id), group_id=group_id
        )
    if rows:
        async with await _memory_db(
            "memory_observation_scan_state", group_id, write=True
        ) as conn:
            await conn.execute(
                """UPDATE memory_observation_scan_state
                   SET scan_after_message_id=?,updated_at=? WHERE group_id=?""",
                (int(rows[-1][0]), now, group_id),
            )
            await conn.commit()
    return len(missing)


async def _evaluate_case(group_id: int, case_id: str, input_version: str) -> dict:
    """Evaluate and distill one persisted Case."""
    from ai.memory import _memory_db
    async with await _memory_db("agent_cases", group_id, write=False) as db:
        async with db.execute(
            """SELECT outcome,errors,attempts,outcome_status,
                correction_evidence_json FROM agent_cases
                WHERE case_id=? AND group_id=?""",
            (case_id, group_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise ValueError(f"case not found: {case_id}")
    evaluation = evaluate_outcome(
        outcome=row[0],
        errors=json.loads(row[1] or "[]"),
        attempts=row[2],
        outcome_status=row[3],
        correction_evidence=json.loads(row[4] or "{}"),
    )
    record_id = None
    if evaluation.should_distill:
        from ai.experiences import distill_case
        record_id = await distill_case(case_id, group_id)
    skill_id = None
    skill_promoted = False
    if record_id:
        from ai.skill_learning import compile_candidate
        skill_id = await compile_candidate(record_id, group_id)
    return {
        "classification": evaluation.classification,
        "information_gain": evaluation.information_gain,
        "should_distill": evaluation.should_distill,
        "confidence": evaluation.confidence,
        "record_id": record_id,
        "skill_id": skill_id,
        "skill_promoted": skill_promoted,
        "promotion_required": bool(skill_id),
        "input_version": input_version,
    }


async def _project_skill(
    group_id: int, skill_id: str, input_version: str
) -> dict:
    from ai.skill_learning import project_skill

    path = await project_skill(skill_id, group_id)
    if path is None:
        raise ValueError(f"Skill not found for projection: {skill_id}")
    return {
        "skill_id": skill_id,
        "path": path,
        "input_version": input_version,
    }


_HANDLERS: Mapping[str, _JobHandler] = {
    "evaluate_case": _evaluate_case,
    "project_skill": _project_skill,
    "observe_turn": _observe_turn,
    "observe_turn_fact": _observe_turn_fact,
    "observe_turn_summary": _observe_turn_summary,
    "observe_turn_reflection": _observe_turn_reflection,
    "observe_turn_tool_compression": _observe_turn_tool_compression,
}


async def dispatch_group(group_id: int, *, limit: int = 10, lease_seconds: int = 60) -> dict[str, int]:
    """Claim and execute a bounded batch for one Worker-owned Group."""
    scope = MemoryScope.group(group_id=group_id, actor_id="pipeline_dispatcher")
    jobs = await _pipeline_repo.list_ready(scope, limit=limit)
    processed = 0
    failed = 0
    for job in jobs:
        job_id = str(job["job_id"])
        lease_token = await _pipeline_repo.claim(
            scope, job_id, lease_seconds=lease_seconds
        )
        if not lease_token:
            continue
        try:
            handler = _HANDLERS.get(str(job["job_type"]))
            if handler is None:
                raise ValueError(f"unsupported pipeline job type: {job['job_type']}")
            output = await handler(
                group_id, str(job["input_id"]), str(job["input_version"])
            )
            completed = await _pipeline_repo.complete(
                scope,
                job_id,
                lease_token=lease_token,
                output_json=json.dumps(output),
            )
            if not completed:
                raise LostLeaseError(f"Worker lost lease for job {job_id}")
            processed += 1
        except Exception as exc:
            failed += 1
            try:
                recorded = await _pipeline_repo.fail(
                    scope,
                    job_id,
                    lease_token=lease_token,
                    error_message=str(exc),
                )
                if not recorded and not isinstance(exc, LostLeaseError):
                    log.warning("pipeline dispatcher lost failure lease for %s", job_id)
            except Exception:
                log.exception("pipeline dispatcher could not record failure for %s", job_id)
            log.exception("pipeline dispatcher failed job %s for group %d", job_id, group_id)
    return {"claimed": processed + failed, "completed": processed, "failed": failed}


async def job_stats(group_id: int) -> dict[str, int]:
    scope = MemoryScope.group(group_id=group_id, actor_id="pipeline_dispatcher")
    return dict(await _pipeline_repo.stats(scope))
