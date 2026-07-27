"""Evidence-gated experience distillation and retrieval."""
from __future__ import annotations
import hashlib
import json
import time
import re
import asyncio


async def _index_vector(record_id: str, content: str, group_id: int, bot_id: int | None,
                        confidence: float) -> None:
    from ai.memory import ChromaStore
    await asyncio.to_thread(ChromaStore.write_fact_sync, record_id, content, {
        "group_id": group_id, "bot_id": bot_id or 0, "mem_type": "experience",
        "timestamp": time.time(), "importance": confidence,
    })


def _projection_version(
    record_id: str, content: str, bot_id: int | None, confidence: float
) -> str:
    canonical = json.dumps(
        [record_id, content, bot_id, confidence],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _enqueue_vector_projection(
    db,
    *,
    record_id: str,
    content: str,
    group_id: int,
    bot_id: int | None,
    confidence: float,
    now_ms: int,
) -> None:
    from memory.bootstrap import get_memory_module
    await get_memory_module().projection_outbox.enqueue(
        db,
        event_id=f"experience-vector:{record_id}",
        projection_type="experience_vector_upsert",
        aggregate_id=record_id,
        aggregate_version=_projection_version(record_id, content, bot_id, confidence),
        group_id=group_id,
        payload={
            "record_id": record_id,
            "content": content,
            "group_id": group_id,
            "bot_id": bot_id,
            "confidence": confidence,
        },
        now_ms=now_ms,
    )


async def distill_case(case_id: str, group_id: int | None) -> str | None:
    """Create an experience only for a completed case containing a correction signal."""
    if group_id is None:
        return None
    from ai.memory import _memory_db
    async with await _memory_db("agent_cases", group_id, write=False) as db:
        async with db.execute(
            """SELECT bot_id,task,task_signature,tools_used,errors,outcome,summary,
                outcome_status,verification_adapter,correction_evidence_json
                FROM agent_cases
                WHERE case_id=? AND group_id=?""",
            (case_id, group_id),
        ) as cur:
            row = await cur.fetchone()
    if (
        not row
        or row[5] != "completed"
        or row[7] != "verified_success"
    ):
        return None
    correction = json.loads(row[9] or "{}")
    if not correction:
        return None
    raw_errors = json.loads(row[4] or "[]")
    if not raw_errors:
        return None
    errors = [re.sub(r"[\r\n\t<>]", " ", str(err)).strip()[:150] for err in raw_errors]
    clean_task = re.sub(r"[\r\n\t<>]", " ", row[1] or "").strip()[:200]
    record_id = "exp:" + hashlib.sha256(case_id.encode()).hexdigest()[:24]
    content = json.dumps({
        "task_pattern": clean_task, "approach": json.loads(row[3] or "[]"),
        "failure_mode": errors,
        "corrective_action": correction.get("corrective_actions", []),
        "verification": {
            "adapter": row[8],
            "target": correction.get("target", ""),
            "status": row[7],
        },
        "limitations": "Derived from one verified case",
    }, ensure_ascii=False)
    now = int(time.time() * 1000)
    target_rid = None
    target_confidence = 0.73
    async with await _memory_db("memory_records", group_id, write=True) as db:
        async with db.execute("SELECT record_id,source_ids,supporting_count,contradicting_count,"
                              "confidence,status FROM memory_records "
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
            target_confidence = min(0.95, float(existing[4]) + 0.08)
            should_reactivate = (
                existing[5] == "suspended"
                and target_confidence >= 0.7
                and new_count >= int(existing[3]) + 2
            )
            next_status = "active" if should_reactivate else existing[5]
            await db.execute("UPDATE memory_records SET status=?,content=?,supporting_count=?,source_ids=?,confidence=?,"
                             "updated_at=? WHERE record_id=?",
                             (next_status,content,new_count,json.dumps(sources),target_confidence,now,existing[0]))
            target_rid = existing[0]
        else:
            await db.execute("""INSERT INTO memory_records
              (record_id,kind,group_id,bot_id,status,content,task_signature,confidence,importance,
               supporting_count,source_ids,created_at,updated_at) VALUES (?, 'experience',?,?, 'active',?,?,0.73,0.6,1,?,?,?)
              ON CONFLICT(record_id) DO UPDATE SET status='active',content=excluded.content,updated_at=excluded.updated_at""",
              (record_id,group_id,row[0],content,row[2],json.dumps([case_id]),now,now))
            target_rid = record_id

        await _enqueue_vector_projection(
            db,
            record_id=target_rid,
            content=content,
            group_id=group_id,
            bot_id=row[0],
            confidence=target_confidence,
            now_ms=now,
        )
        await db.commit()

    # Best-effort low-latency delivery. Failure remains durable for the worker's
    # periodic and hydration-time consumers.
    from memory.bootstrap import get_memory_module
    await get_memory_module().projection_outbox.drain(
        group_id, limit=1, event_id=f"experience-vector:{target_rid}"
    )
    return target_rid


async def reconcile_experience_projections(group_id: int) -> int:
    """Re-enqueue canonical records so hydration repairs a missing vector index."""
    from ai.memory import _memory_db
    now = int(time.time() * 1000)
    async with await _memory_db("memory_records", group_id, write=True) as db:
        async with db.execute(
            "SELECT record_id,content,bot_id,confidence FROM memory_records "
            "WHERE group_id=? AND kind='experience'",
            (group_id,),
        ) as cur:
            rows = await cur.fetchall()
        for record_id, content, bot_id, confidence in rows:
            await _enqueue_vector_projection(
                db,
                record_id=record_id,
                content=content,
                group_id=group_id,
                bot_id=bot_id,
                confidence=float(confidence),
                now_ms=now,
            )
        await db.commit()
    return len(rows)


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
    """Compatibility telemetry: run completion alone is not causal usage evidence."""

    from ai.usage_tracking import record_legacy_completion
    from memory.domain import UsageKind
    await record_legacy_completion(
        kind=UsageKind.EXPERIENCE,
        item_ids=record_ids,
        run_id=run_id,
        group_id=group_id,
        outcome=outcome,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_attempts=tool_attempts,
    )


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
