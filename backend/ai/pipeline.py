"""Durable and idempotent Memory & Learning post-processing jobs."""
from __future__ import annotations
import json
import logging
from collections.abc import Awaitable, Callable, Mapping

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
        from ai.skill_learning import compile_candidate, promote_skill
        skill_id = await compile_candidate(record_id, group_id)
        if skill_id:
            skill_promoted = await promote_skill(
                skill_id,
                group_id,
                actor_id="system:learning_pipeline",
                reason="Candidate passed repeated-evidence compilation gate",
            )
    return {
        "classification": evaluation.classification,
        "information_gain": evaluation.information_gain,
        "should_distill": evaluation.should_distill,
        "confidence": evaluation.confidence,
        "record_id": record_id,
        "skill_id": skill_id,
        "skill_promoted": skill_promoted,
        "input_version": input_version,
    }


_HANDLERS: Mapping[str, _JobHandler] = {
    "evaluate_case": _evaluate_case,
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
