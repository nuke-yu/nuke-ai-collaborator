"""Evidence-gated experience distillation and retrieval."""
from __future__ import annotations
import hashlib
import json
import time
import re


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


def _terms(text: str) -> set[str]:
    value = (text or "").lower()
    terms = set(re.findall(r"[a-z0-9_]{2,}", value))
    zh = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    terms.update(zh[i:i + 2] for i in range(max(0, len(zh) - 1)))
    return terms


async def recall_experiences(*, query: str, run_id: str, group_id: int | None,
                             bot_id: int | None, limit: int = 2,
                             char_budget: int = 2400) -> tuple[str, list[str]]:
    if group_id is None:
        return "", []
    from ai.memory import _memory_db
    async with await _memory_db("memory_records", group_id, write=False) as db:
        async with db.execute("SELECT record_id,content,confidence FROM memory_records "
                              "WHERE group_id=? AND bot_id=? AND kind='experience' AND status='active'",
                              (group_id, bot_id)) as cur:
            rows = await cur.fetchall()
    q = _terms(query); ranked = []
    for record_id, content, confidence in rows:
        terms = _terms(content)
        score = len(q & terms) / max(1, len(q | terms))
        if score:
            ranked.append((score * float(confidence), record_id, content))
    ranked.sort(reverse=True); selected = []; used = 0
    for _, record_id, content in ranked[:limit]:
        snippet = content[:1200]
        if used + len(snippet) > char_budget:
            break
        selected.append((record_id, snippet)); used += len(snippet)
    if not selected:
        return "", []
    now = int(time.time() * 1000)
    async with await _memory_db("experience_usage", group_id, write=True) as db:
        for record_id, _ in selected:
            await db.execute("INSERT INTO experience_usage "
                "(record_id,run_id,group_id,bot_id,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(record_id,run_id) DO UPDATE SET state='injected',updated_at=excluded.updated_at",
                (record_id,run_id,group_id,bot_id,"injected",now,now))
        await db.commit()
    return "[Relevant prior execution experience]\n" + "\n".join(f"- {x[1]}" for x in selected), [x[0] for x in selected]


async def complete_usage(*, record_ids: list[str], run_id: str, group_id: int | None,
                         outcome: str, input_tokens: int, output_tokens: int,
                         tool_attempts: int) -> None:
    if group_id is None or not record_ids:
        return
    from ai.memory import _memory_db
    now = int(time.time() * 1000)
    async with await _memory_db("experience_usage", group_id, write=True) as db:
        for record_id in record_ids:
            await db.execute("UPDATE experience_usage SET state='executed',outcome=?,input_tokens=?,"
                "output_tokens=?,tool_attempts=?,updated_at=? WHERE record_id=? AND run_id=? AND group_id=?",
                (outcome,input_tokens,output_tokens,tool_attempts,now,record_id,run_id,group_id))
        await db.commit()
