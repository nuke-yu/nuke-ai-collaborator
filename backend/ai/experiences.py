"""Evidence-gated experience distillation and retrieval."""
from __future__ import annotations
import hashlib
import json
import time


async def distill_case(case_id: str, group_id: int | None) -> str | None:
    """Create an experience only for a completed case containing a correction signal."""
    if group_id is None:
        return None
    from ai.memory import _memory_db
    async with await _memory_db("agent_cases", group_id, write=False) as db:
        async with db.execute(
            "SELECT bot_id,task,task_signature,tools_used,errors,outcome,summary FROM agent_cases "
            "WHERE case_id=? AND group_id=?", (case_id, group_id),
        ) as cur:
            row = await cur.fetchone()
    if not row or row[5] != "completed":
        return None
    errors = json.loads(row[4] or "[]")
    if not errors:
        return None
    record_id = "exp:" + hashlib.sha256(case_id.encode()).hexdigest()[:24]
    content = json.dumps({
        "task_pattern": row[1], "approach": json.loads(row[3] or "[]"),
        "failure_mode": errors, "corrective_action": "Subsequent execution completed successfully",
        "verification": "run_terminal_completed", "limitations": "Derived from one case",
    }, ensure_ascii=False)
    now = int(time.time() * 1000)
    async with await _memory_db("memory_records", group_id, write=True) as db:
        await db.execute("""INSERT INTO memory_records
          (record_id,kind,group_id,bot_id,status,content,task_signature,confidence,importance,
           source_ids,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(record_id) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at""",
          (record_id,"experience",group_id,row[0],"active",content,row[2],0.65,0.8,
           json.dumps([case_id]),json.dumps({"verification":"verified_after_correction"}),now,now))
        await db.commit()
    return record_id
