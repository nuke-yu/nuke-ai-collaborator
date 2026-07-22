"""Durable and idempotent Memory & Learning post-processing jobs."""
from __future__ import annotations
import hashlib
import json
import time

from ai.cases import evaluate_outcome


from memory.adapters.runtime.learning_legacy import LegacyPipelineJobAdapter
from memory.domain import MemoryScope

_pipeline_repo = LegacyPipelineJobAdapter()


async def enqueue(*, job_type: str, group_id: int, input_id: str,
                  input_version: str = "1") -> str:
    scope = MemoryScope.group(group_id=group_id, actor_id="pipeline")
    return await _pipeline_repo.enqueue(scope, job_type=job_type, input_id=input_id, input_version=input_version)


async def process_case(case_id: str, group_id: int, *, input_version: str = "1") -> str:
    """Claim and process one Case job; safe to call repeatedly after a crash."""
    from ai.memory import _memory_db
    scope = MemoryScope.group(group_id=group_id, actor_id="pipeline")
    job_id = await _pipeline_repo.enqueue(
        scope,
        job_type="evaluate_case",
        input_id=case_id,
        input_version=input_version,
    )
    lease_token = await _pipeline_repo.claim(scope, job_id, lease_seconds=60)
    if not lease_token:
        return job_id
    try:
        async with await _memory_db("agent_cases", group_id, write=False) as db:
            async with db.execute("SELECT outcome,errors,attempts FROM agent_cases WHERE case_id=? AND group_id=?",
                                  (case_id, group_id)) as cur:
                row = await cur.fetchone()
        if not row:
            raise ValueError(f"case not found: {case_id}")
        evaluation = evaluate_outcome(outcome=row[0], errors=json.loads(row[1] or "[]"), attempts=row[2])
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
        output = {"classification": evaluation.classification, "information_gain": evaluation.information_gain,
                  "should_distill": evaluation.should_distill, "confidence": evaluation.confidence,
                  "record_id": record_id, "skill_id": skill_id, "skill_promoted": skill_promoted}
        from memory.contracts import LostLeaseError
        completed = await _pipeline_repo.complete(scope, job_id, lease_token=lease_token, output_json=json.dumps(output))
        if not completed:
            raise LostLeaseError(f"Worker lost lease for job {job_id}")
    except Exception as exc:
        await _pipeline_repo.fail(scope, job_id, lease_token=lease_token, error_message=str(exc))
        raise
    return job_id
