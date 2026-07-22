"""Evidence-gated experience distillation and retrieval."""
from __future__ import annotations
import hashlib
import json
import time
import re
import asyncio


async def _index_vector(record_id: str, content: str, group_id: int, bot_id: int | None,
                        confidence: float) -> None:
    try:
        from ai.memory import ChromaStore
        await asyncio.to_thread(ChromaStore.write_fact_sync, record_id, content, {
            "group_id": group_id, "bot_id": bot_id or 0, "mem_type": "experience",
            "timestamp": time.time(), "importance": confidence,
        })
    except Exception:
        return


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
    raw_errors = json.loads(row[4] or "[]")
    if not raw_errors:
        return None
    errors = [re.sub(r"[\r\n\t<>]", " ", str(err)).strip()[:150] for err in raw_errors]
    clean_task = re.sub(r"[\r\n\t<>]", " ", row[1] or "").strip()[:200]
    record_id = "exp:" + hashlib.sha256(case_id.encode()).hexdigest()[:24]
    content = json.dumps({
        "task_pattern": clean_task, "approach": json.loads(row[3] or "[]"),
        "failure_mode": errors, "corrective_action": "Subsequent execution completed successfully",
        "verification": "run_terminal_completed", "limitations": "Derived from one case",
    }, ensure_ascii=False)
    now = int(time.time() * 1000)
    target_rid = None
    target_confidence = 0.73
    async with await _memory_db("memory_records", group_id, write=True) as db:
        async with db.execute("SELECT record_id,source_ids,supporting_count FROM memory_records "
                              "WHERE group_id=? AND bot_id=? AND kind='experience' "
                              "AND task_signature=? ORDER BY updated_at DESC LIMIT 1",
                              (group_id,row[0],row[2])) as cur:
            existing = await cur.fetchone()
        if existing:
            prev_sources = json.loads(existing[1] or "[]")
            if case_id in prev_sources:
                return existing[0]
            sources = prev_sources + [case_id]
            new_count = len(sources)
            target_confidence = min(0.95, 0.73 + 0.08 * (new_count - 1))
            await db.execute("UPDATE memory_records SET status='active',supporting_count=?,source_ids=?,confidence=?,"
                             "updated_at=? WHERE record_id=?",
                             (new_count,json.dumps(sources),target_confidence,now,existing[0]))
            await db.commit()
            target_rid = existing[0]
        else:
            await db.execute("""INSERT INTO memory_records
              (record_id,kind,group_id,bot_id,status,content,task_signature,confidence,importance,
               supporting_count,source_ids,created_at,updated_at) VALUES (?, 'experience',?,?, 'active',?,?,0.73,0.6,1,?,?,?)
              ON CONFLICT(record_id) DO UPDATE SET status='active',content=excluded.content,updated_at=excluded.updated_at""",
              (record_id,group_id,row[0],content,row[2],json.dumps([case_id]),now,now))
            await db.commit()
            target_rid = record_id

    # Execute vector indexing OUTSIDE SQLite writer transaction
    await _index_vector(target_rid, content, group_id, row[0], target_confidence)
    return target_rid


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
    vector_scores = {}
    try:
        from ai.memory import ChromaStore
        where = {"$and":[{"group_id":{"$eq":group_id}},{"bot_id":{"$eq":bot_id or 0}},
                          {"mem_type":{"$eq":"experience"}}]}
        result = await asyncio.to_thread(ChromaStore.query_similar_sync, query, where, max(limit * 4, 8))
        ids = (result.get("ids") or [[]])[0]; distances = (result.get("distances") or [[]])[0]
        vector_scores = {rid:max(0.0,1.0-float(dist)) for rid,dist in zip(ids,distances)}
    except Exception:
        pass
    q = _terms(query); ranked = []
    for record_id, content, confidence in rows:
        terms = _terms(content)
        lexical = len(q & terms) / max(1, len(q | terms))
        score = 0.55 * lexical + 0.45 * vector_scores.get(record_id, 0.0)
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
                "ON CONFLICT(record_id,run_id) DO UPDATE SET updated_at=excluded.updated_at "
                "WHERE experience_usage.state='injected'",
                (record_id,run_id,group_id,bot_id,"injected",now,now))
        await db.commit()
    formatted_experiences = []
    for _, snippet in selected:
        safe_snippet = snippet.replace("</untrusted_learned_experience>", "")
        formatted_experiences.append(f"<untrusted_learned_experience>\n{safe_snippet}\n</untrusted_learned_experience>")
    return "[Relevant prior execution experience]\n" + "\n".join(formatted_experiences), [x[0] for x in selected]


async def complete_usage(*, record_ids: list[str], run_id: str, group_id: int | None,
                         outcome: str, input_tokens: int, output_tokens: int,
                         tool_attempts: int) -> None:
    if group_id is None or not record_ids:
        return
    from ai.memory import _memory_db
    now = int(time.time() * 1000)
    async with await _memory_db("experience_usage", group_id, write=True) as db:
        for record_id in record_ids:
            cur = await db.execute("UPDATE experience_usage SET state='executed',outcome=?,input_tokens=?,"
                "output_tokens=?,tool_attempts=?,updated_at=? WHERE record_id=? AND run_id=? AND group_id=? AND state!='executed'",
                (outcome,input_tokens,output_tokens,tool_attempts,now,record_id,run_id,group_id))
            if cur.rowcount == 1:
                if outcome == "completed":
                    await db.execute("UPDATE memory_records SET supporting_count=supporting_count+1,"
                                     "confidence=MIN(0.98,confidence+0.03),last_used_at=?,updated_at=? WHERE record_id=?",
                                     (now,now,record_id))
                else:
                    await db.execute("UPDATE memory_records SET contradicting_count=contradicting_count+1,"
                                     "confidence=MAX(0.05,confidence-0.2),last_used_at=?,updated_at=?,"
                                     "status=CASE WHEN contradicting_count+1>=2 THEN 'suspended' ELSE status END "
                                     "WHERE record_id=?", (now,now,record_id))
        await db.commit()


async def decay_experiences(group_id: int, *, now_ms: int | None = None,
                            stale_days: int = 90) -> int:
    from ai.memory import _memory_db
    now = now_ms or int(time.time() * 1000)
    cutoff = now - stale_days * 86_400_000
    async with await _memory_db("memory_records", group_id, write=True) as db:
        cur = await db.execute("""UPDATE memory_records SET status='deprecated',valid_to=?,updated_at=?
          WHERE group_id=? AND kind='experience' AND status='active' AND confidence<0.5
          AND COALESCE(last_used_at,created_at)<?""", (now,now,group_id,cutoff))
        await db.commit()
        return cur.rowcount
