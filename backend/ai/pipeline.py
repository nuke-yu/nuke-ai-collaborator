"""Durable and idempotent Memory & Learning post-processing jobs."""
from __future__ import annotations
import hashlib
import json
import time

from ai.cases import evaluate_outcome


async def enqueue(*, job_type: str, group_id: int, input_id: str,
                  input_version: str = "1") -> str:
    from ai.memory import _memory_db
    key = f"{job_type}:{group_id}:{input_id}:{input_version}"
    job_id = "job:" + hashlib.sha256(key.encode()).hexdigest()[:24]
    now = int(time.time() * 1000)
    async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
        await db.execute("""INSERT INTO pipeline_jobs
          (job_id,job_type,group_id,input_id,input_version,idempotency_key,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING""",
          (job_id,job_type,group_id,input_id,input_version,key,now,now))
        await db.commit()
    return job_id


async def process_case(case_id: str, group_id: int) -> str:
    """Claim and process one Case job; safe to call repeatedly after a crash."""
    from ai.memory import _memory_db
    job_id = await enqueue(job_type="evaluate_case", group_id=group_id, input_id=case_id)
    now = int(time.time() * 1000)
    async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
        cur = await db.execute("""UPDATE pipeline_jobs SET status='running',attempt=attempt+1,
          lease_until=?,updated_at=? WHERE job_id=? AND group_id=? AND
          (status='pending' OR (status='running' AND lease_until<?) OR status='failed') AND attempt<max_attempts""",
          (now + 60_000,now,job_id,group_id,now))
        await db.commit()
        if cur.rowcount != 1:
            return job_id
    try:
        async with await _memory_db("agent_cases", group_id, write=False) as db:
            async with db.execute("SELECT outcome,errors,attempts FROM agent_cases WHERE case_id=? AND group_id=?",
                                  (case_id,group_id)) as cur:
                row = await cur.fetchone()
        if not row:
            raise ValueError(f"case not found: {case_id}")
        evaluation = evaluate_outcome(outcome=row[0], errors=json.loads(row[1] or "[]"), attempts=row[2])
        record_id = None
        if evaluation.should_distill:
            from ai.experiences import distill_case
            record_id = await distill_case(case_id, group_id)
        output = {"classification":evaluation.classification,"information_gain":evaluation.information_gain,
                  "should_distill":evaluation.should_distill,"confidence":evaluation.confidence,
                  "record_id":record_id}
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            await db.execute("UPDATE pipeline_jobs SET status='completed',lease_until=NULL,error='',output_json=?,"
                             "completed_at=?,updated_at=? WHERE job_id=?", (json.dumps(output),now,now,job_id))
            await db.commit()
    except Exception as exc:
        async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
            await db.execute("""UPDATE pipeline_jobs SET status=CASE WHEN attempt>=max_attempts THEN 'dead'
              ELSE 'failed' END,lease_until=NULL,error=?,updated_at=? WHERE job_id=?""",
              (str(exc)[:2000],now,job_id))
            await db.commit()
        raise
    return job_id
