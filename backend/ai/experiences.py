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
            """SELECT bot_id,task,task_signature,tools_used,files_touched,
                errors,outcome,summary,outcome_status,verification_adapter,
                correction_evidence_json,semantic_cluster_key
                FROM agent_cases
                WHERE case_id=? AND group_id=?""",
            (case_id, group_id),
        ) as cur:
            row = await cur.fetchone()
        async with db.execute(
            """SELECT ordinal,step_id,attempt_id,phase,action_tool,
                action_target,observation_status,observation_summary,
                verifier_adapter,verifies_task
                FROM agent_case_attempts
                WHERE case_id=? AND group_id=? ORDER BY ordinal""",
            (case_id, group_id),
        ) as cur:
            attempt_rows = await cur.fetchall()
    if (
        not row
        or row[6] != "completed"
        or row[8] != "verified_success"
    ):
        return None
    correction = json.loads(row[10] or "{}")
    if not correction:
        return None
    raw_errors = json.loads(row[5] or "[]")
    if not raw_errors:
        return None
    errors = [re.sub(r"[\r\n\t<>]", " ", str(err)).strip()[:150] for err in raw_errors]
    clean_task = re.sub(r"[\r\n\t<>]", " ", row[1] or "").strip()[:200]
    record_id = "exp:" + hashlib.sha256(case_id.encode()).hexdigest()[:24]
    experience = _build_experience_v2(
        task=clean_task,
        files=json.loads(row[4] or "[]"),
        errors=errors,
        outcome_status=row[8],
        verification_adapter=row[9],
        correction=correction,
        attempt_rows=attempt_rows,
    )
    semantic_cluster_key = str(row[11] or "")
    environment_signature = experience["environment"]["signature"]
    failure_signature = experience["failure"]["signature"]
    now = int(time.time() * 1000)
    target_rid = None
    target_confidence = 0.73
    async with await _memory_db("memory_records", group_id, write=True) as db:
        async with db.execute("SELECT record_id,source_ids,supporting_count,contradicting_count,"
                              "confidence,status FROM memory_records "
                              "WHERE group_id=? AND bot_id=? AND kind='experience' "
                              "AND semantic_cluster_key=? "
                              "AND environment_signature=? AND failure_signature=? "
                              "ORDER BY updated_at DESC LIMIT 1",
                              (
                                  group_id,
                                  row[0],
                                  semantic_cluster_key,
                                  environment_signature,
                                  failure_signature,
                              )) as cur:
            existing = await cur.fetchone()
        if existing:
            prev_sources = json.loads(existing[1] or "[]")
            if case_id in prev_sources:
                return existing[0]
            sources = prev_sources + [case_id]
            experience["source_case_ids"] = sources
            content = json.dumps(experience, ensure_ascii=False)
            metadata = _experience_metadata(experience)
            new_count = len(sources)
            target_confidence = min(0.95, float(existing[4]) + 0.08)
            should_reactivate = (
                existing[5] == "suspended"
                and target_confidence >= 0.7
                and new_count >= int(existing[3]) + 2
            )
            next_status = "active" if should_reactivate else existing[5]
            await db.execute(
                """UPDATE memory_records SET status=?,content=?,
                supporting_count=?,source_ids=?,confidence=?,
                metadata_json=?,algorithm_version='experience-v2',
                semantic_cluster_key=?,environment_signature=?,
                failure_signature=?,
                updated_at=? WHERE record_id=?""",
                (
                    next_status,
                    content,
                    new_count,
                    json.dumps(sources),
                    target_confidence,
                    json.dumps(metadata, ensure_ascii=False),
                    semantic_cluster_key,
                    environment_signature,
                    failure_signature,
                    now,
                    existing[0],
                ),
            )
            target_rid = existing[0]
        else:
            experience["source_case_ids"] = [case_id]
            content = json.dumps(experience, ensure_ascii=False)
            metadata = _experience_metadata(experience)
            await db.execute("""INSERT INTO memory_records
              (record_id,kind,group_id,bot_id,status,content,task_signature,confidence,importance,
               semantic_cluster_key,environment_signature,failure_signature,
               supporting_count,source_ids,metadata_json,algorithm_version,created_at,updated_at)
              VALUES (?, 'experience',?,?, 'active',?,?,0.73,0.6,?,?,?,1,?,?,'experience-v2',?,?)
              ON CONFLICT(record_id) DO UPDATE SET status='active',content=excluded.content,updated_at=excluded.updated_at""",
              (
                  record_id,
                  group_id,
                  row[0],
                  content,
                  row[2],
                  semantic_cluster_key,
                  environment_signature,
                  failure_signature,
                  json.dumps([case_id]),
                  json.dumps(metadata, ensure_ascii=False),
                  now,
                  now,
              ))
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


def _build_experience_v2(
    *,
    task: str,
    files: list[str],
    errors: list[str],
    outcome_status: str,
    verification_adapter: str,
    correction: dict,
    attempt_rows: list[tuple],
) -> dict:
    attempts = [
        {
            "ordinal": row[0],
            "step_id": row[1],
            "attempt_id": row[2],
            "phase": row[3],
            "tool": row[4],
            "target": row[5],
            "status": row[6],
            "summary": row[7],
            "verifier_adapter": row[8],
            "verifies_task": bool(row[9]),
        }
        for row in attempt_rows
    ]
    corrective_actions = [
        {
            "step_id": attempt["step_id"],
            "attempt_id": attempt["attempt_id"],
            "tool": attempt["tool"],
            "target": attempt["target"],
            "status": attempt["status"],
        }
        for attempt in attempts
        if attempt["phase"] == "recover" and attempt["status"] == "success"
    ]
    failed_verification = next(
        (
            attempt
            for attempt in attempts
            if attempt["verifies_task"] and attempt["status"] == "error"
        ),
        None,
    )
    successful_verification = next(
        (
            attempt
            for attempt in reversed(attempts)
            if attempt["verifies_task"] and attempt["status"] == "success"
        ),
        None,
    )
    extensions = sorted(
        {
            "." + path.rsplit(".", 1)[-1].lower()
            for path in files
            if "." in path.rsplit("/", 1)[-1]
        }
    )
    tools = list(dict.fromkeys(attempt["tool"] for attempt in attempts if attempt["tool"]))
    environment = {
        "file_extensions": extensions,
        "tools": tools,
        "verification_adapter": verification_adapter,
    }
    environment["signature"] = hashlib.sha256(
        json.dumps(environment, sort_keys=True).encode()
    ).hexdigest()[:16]
    failure_signature = _failure_signature(
        errors,
        verification_adapter=verification_adapter,
        target=str(correction.get("target", "")),
    )
    return {
        "schema_version": "experience-v2",
        "task_pattern": task,
        "environment": environment,
        "failure": {
            "signature": failure_signature,
            "adapter": verification_adapter,
            "target": correction.get("target", ""),
            "messages": errors,
            "step_id": failed_verification["step_id"] if failed_verification else "",
            "attempt_id": (
                failed_verification["attempt_id"] if failed_verification else ""
            ),
        },
        "root_cause": {
            "status": "unresolved",
            "method": "deterministic_trace_only",
            "confidence": 0.0,
        },
        "approach": [
            {
                "phase": attempt["phase"],
                "tool": attempt["tool"],
                "target": attempt["target"],
                "status": attempt["status"],
            }
            for attempt in attempts
        ],
        "corrective_actions": corrective_actions,
        "verification": {
            "adapter": verification_adapter,
            "target": correction.get("target", ""),
            "status": outcome_status,
            "step_id": (
                successful_verification["step_id"]
                if successful_verification
                else ""
            ),
            "attempt_id": (
                successful_verification["attempt_id"]
                if successful_verification
                else ""
            ),
        },
        "limitations": [
            "derived_from_verified_execution_trace",
            "root_cause_not_yet_confirmed",
            "revalidate_when_environment_signature_changes",
        ],
        "source_case_ids": [],
    }


def _experience_metadata(experience: dict) -> dict:
    return {
        "schema_version": experience["schema_version"],
        "environment_signature": experience["environment"]["signature"],
        "failure_signature": experience["failure"]["signature"],
        "verification_adapter": experience["verification"]["adapter"],
        "evidence_quality": "deterministic_verified_trace",
    }


def _failure_signature(
    errors: list[str], *, verification_adapter: str, target: str
) -> str:
    categories = []
    patterns = (
        ("permission_denied", r"permission denied|forbidden|unauthorized|权限"),
        ("timeout", r"timed? ?out|timeout|超时"),
        ("syntax_error", r"syntax ?error|语法"),
        ("assertion_failure", r"assert(?:ion)?(?:error| failed)?|断言"),
        ("dependency_error", r"dependency|module not found|importerror|依赖"),
        ("connection_error", r"connection|network|dns|连接|网络"),
        ("not_found", r"not found|no such file|不存在"),
        ("verification_failure", r"\bfail(?:ed|ure)?\b|\berror\b|失败|错误"),
    )
    combined = " | ".join(error.lower() for error in errors)
    for category, pattern in patterns:
        if re.search(pattern, combined):
            categories.append(category)
    if not categories:
        normalized = re.sub(r"\b\d+\b", "<n>", combined)
        normalized = re.sub(r"\s+", " ", normalized).strip()[:300]
        categories.append(normalized or "unknown_failure")
    payload = {
        "adapter": verification_adapter,
        "target": target,
        "categories": categories,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


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
    from memory.domain import identify_task
    query_identity = identify_task(query)

    vector_scores = {}
    vector_candidate_ids: set[str] = set()
    try:
        from ai.memory import ChromaStore
        where = {"$and":[{"group_id":{"$eq":group_id}},{"bot_id":{"$eq":bot_id or 0}},
                          {"mem_type":{"$eq":"experience"}}]}
        result = await asyncio.to_thread(ChromaStore.query_similar_sync, query, where, max(limit * 4, 16))
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for rid, dist in zip(ids, distances):
            v_score = max(0.0, 1.0 - float(dist))
            if v_score >= 0.1:
                vector_scores[rid] = v_score
                vector_candidate_ids.add(rid)
    except Exception:
        pass

    # Bounded candidate retrieval: vector candidates + cluster key candidates + recent fallback
    async with await _memory_db("memory_records", group_id, write=False) as db:
        candidate_ids = list(vector_candidate_ids[:32]) if isinstance(vector_candidate_ids, list) else list(vector_candidate_ids)
        rows = []
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            async with db.execute(
                f"""SELECT record_id,content,confidence,semantic_cluster_key FROM memory_records
                    WHERE group_id=? AND bot_id=? AND kind='experience' AND status='active'
                      AND record_id IN ({placeholders})""",
                (group_id, bot_id, *candidate_ids),
            ) as cur:
                rows.extend(await cur.fetchall())

        fetched_ids = {str(row[0]) for row in rows}
        # Supplement with cluster match and recent active experiences up to 50 candidates max
        async with db.execute(
            """SELECT record_id,content,confidence,semantic_cluster_key FROM memory_records
                WHERE group_id=? AND bot_id=? AND kind='experience' AND status='active'
                  AND (semantic_cluster_key=? OR 1=1)
                ORDER BY updated_at DESC LIMIT 50""",
            (group_id, bot_id, query_identity.semantic_cluster_key),
        ) as cur:
            for row in await cur.fetchall():
                if str(row[0]) not in fetched_ids:
                    rows.append(row)
                    fetched_ids.add(str(row[0]))

    q = _terms(query)
    ranked = []
    for record_id, content, confidence, semantic_cluster_key in rows:
        terms = _terms(content)
        lexical = len(q & terms) / max(1, len(q | terms)) if terms and q else 0.0
        cluster_match = float(
            bool(semantic_cluster_key)
            and semantic_cluster_key == query_identity.semantic_cluster_key
        )
        vector_score = vector_scores.get(record_id, 0.0)
        score = (
            0.45 * lexical
            + 0.35 * vector_score
            + 0.20 * cluster_match
        ) * float(confidence)

        # Minimum score threshold to prevent irrelevance
        if score >= 0.08 or (lexical >= 0.15 or vector_score >= 0.3):
            ranked.append((score, record_id, content))

    ranked.sort(reverse=True)
    selected = []
    used = 0
    for _, record_id, content in ranked[:limit]:
        snippet = content[:1200]
        if used + len(snippet) > char_budget:
            break
        selected.append((record_id, snippet))
        used += len(snippet)

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
    from memory.application.references import experience_ref
    for record_id, snippet in selected:
        safe_snippet = snippet.replace("</untrusted_learned_experience>", "")
        formatted_experiences.append(
            f'<untrusted_learned_experience memory_ref="{experience_ref(record_id)}">\n'
            f"{safe_snippet}\n</untrusted_learned_experience>"
        )
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
