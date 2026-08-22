"""Canonical evidence-gated Case -> Experience distillation."""
from __future__ import annotations

import hashlib
import json
import re
import time
import logging
from typing import Any

from memory.application.context import require_database, require_pipeline_repository
from memory.domain import MemoryScope
from memory.domain.safety import safe_memory_mapping, safe_memory_text
from memory.ports import MemoryDatabasePort, PipelineJobRepositoryPort, ProjectionOutboxPort

log = logging.getLogger(__name__)


class CanonicalExperienceDistiller:
    def __init__(
        self,
        database: MemoryDatabasePort | None = None,
        projection_outbox: ProjectionOutboxPort | None = None,
        job_repository: PipelineJobRepositoryPort | None = None,
    ) -> None:
        self._database = database or require_database()
        self._jobs = job_repository or require_pipeline_repository(self._database)
        self._projection_outbox = projection_outbox

    async def distill(self, group_id: int, case_id: str, input_version: str = "1") -> dict[str, Any]:
        async with await self._database.connect("agent_cases", group_id, write=False) as db:
            async with db.execute(
                """SELECT bot_id,task,task_signature,errors,outcome,outcome_status,
                          verification_adapter,correction_evidence_json,
                          semantic_cluster_key FROM agent_cases
                   WHERE case_id=? AND group_id=?""", (case_id, group_id)
            ) as cur:
                case = await cur.fetchone()
            async with db.execute(
                """SELECT ordinal,step_id,attempt_id,phase,action_tool,
                          action_target,observation_status,observation_summary,
                          verifier_adapter,verifies_task
                   FROM agent_case_attempts WHERE case_id=? AND group_id=?
                   ORDER BY ordinal""", (case_id, group_id)
            ) as cur:
                attempts = await cur.fetchall()
        if not case:
            raise ValueError(f"case not found: {case_id}")
        try:
            errors = json.loads(case[3] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            errors = []
        try:
            correction = json.loads(case[7] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            correction = {}
        if case[4] != "completed" or case[5] != "verified_success" or not errors or not correction:
            return {"case_id": case_id, "record_id": None, "distilled": False,
                    "promotion_required": False}

        clean_errors = [re.sub(r"[\r\n\t<>]", " ", str(item)).strip()[:150] for item in errors]
        task = safe_memory_text(case[1], limit=200)
        tools = list(dict.fromkeys(str(row[4]) for row in attempts if row[4]))
        environment = {"file_extensions": [], "tools": tools,
                       "verification_adapter": str(case[6] or "")}
        environment["signature"] = hashlib.sha256(
            json.dumps(environment, sort_keys=True).encode()
        ).hexdigest()[:16]
        combined = " | ".join(item.lower() for item in clean_errors)
        categories = []
        for category, pattern in (("permission_denied", r"permission denied|forbidden|unauthorized|权限"),
                                  ("timeout", r"timed? ?out|timeout|超时"),
                                  ("connection_error", r"connection|network|dns|连接|网络"),
                                  ("verification_failure", r"fail(?:ed|ure)?|error|失败|错误")):
            if re.search(pattern, combined):
                categories.append(category)
        failure_signature = hashlib.sha256(json.dumps(
            {"adapter": case[6], "target": correction.get("target", ""),
             "categories": categories or ["unknown_failure"]},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
        corrective_actions = [
            {"step_id": row[1], "attempt_id": row[2], "tool": row[4],
             "target": row[5], "status": row[6]}
            for row in attempts if row[3] == "recover" and row[6] == "success"
        ]
        failed_verification = next((row for row in attempts if row[9] and row[6] == "error"), None)
        successful_verification = next((row for row in reversed(attempts) if row[9] and row[6] == "success"), None)
        signature = failure_signature
        record_id = "exp:" + hashlib.sha256(case_id.encode()).hexdigest()[:24]
        algorithm_version = "experience-v2" if attempts else "canonical-experience-v1"
        content_obj = {
            "schema_version": algorithm_version,
            "task_pattern": task,
            "environment": environment,
            "failure": {"signature": failure_signature, "adapter": str(case[6] or ""),
                         "target": correction.get("target", ""), "messages": clean_errors,
                         "step_id": failed_verification[1] if failed_verification else "",
                         "attempt_id": failed_verification[2] if failed_verification else ""},
            "root_cause": {"status": "unresolved", "method": "deterministic_trace_only", "confidence": 0.0},
            "approach": [{"phase": row[3], "tool": row[4], "target": row[5], "status": row[6]} for row in attempts],
            "corrective_actions": corrective_actions,
            "verification": {"adapter": str(case[6] or ""), "target": correction.get("target", ""),
                              "status": case[5], "step_id": successful_verification[1] if successful_verification else "",
                              "attempt_id": successful_verification[2] if successful_verification else ""},
            "limitations": ["derived_from_verified_execution_trace", "root_cause_not_yet_confirmed", "revalidate_when_environment_signature_changes"],
            "source_case_ids": [case_id],
        }
        metadata = safe_memory_mapping({
            "schema_version": algorithm_version,
            "evidence_quality": "deterministic_verified_trace",
            "environment_signature": environment["signature"],
            "failure_signature": failure_signature,
        })
        now = int(time.time() * 1000)
        async with await self._database.connect("memory_records", group_id, write=True) as db:
            async with db.execute(
                """SELECT record_id,content,source_ids,supporting_count,
                          contradicting_count,confidence,status
                   FROM memory_records
                   WHERE group_id=? AND bot_id=? AND kind='experience'
                     AND status IN ('active','suspended') AND semantic_cluster_key=?
                     AND environment_signature=? AND failure_signature=?
                   ORDER BY updated_at DESC LIMIT 1""",
                (group_id, case[0], case[8] or "", environment["signature"], failure_signature),
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                log.debug("experience aggregate hit case=%s record=%s sources=%s confidence=%s status=%s", case_id, existing[0], existing[2], existing[5], existing[6])
                record_id = str(existing[0])
                try:
                    previous_content = json.loads(existing[1] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    previous_content = {}
                try:
                    previous_sources = [str(item) for item in json.loads(existing[2] or "[]")]
                except (TypeError, ValueError, json.JSONDecodeError):
                    previous_sources = []
                source_ids = list(dict.fromkeys([*previous_sources, case_id]))
                content_obj["source_case_ids"] = source_ids
                content_obj["supporting_count"] = len(source_ids)
                # Preserve the strongest evidence payload while making the
                # aggregation explicit and auditable.
                target_confidence = float(existing[5] or 0.73)
                # The first post-suspension case is evidence of persistence,
                # not yet evidence strong enough to raise confidence.  The
                # following independent cases provide the hysteresis needed
                # for gradual reactivation.
                if existing[6] == "suspended" and len(source_ids) > 3:
                    target_confidence = min(0.95, target_confidence + 0.08)
                next_status = existing[6]
                if existing[6] == "suspended" and target_confidence >= 0.7:
                    next_status = "active"
                content_obj["source_case_ids"] = source_ids
            else:
                source_ids = [case_id]
                target_confidence = 0.73
                next_status = "active"
            content = safe_memory_mapping(content_obj, limit=100_000)
            await db.execute(
                """INSERT INTO memory_records
                   (record_id,kind,group_id,bot_id,status,content,task_signature,
                    confidence,importance,semantic_cluster_key,environment_signature,failure_signature,
                    supporting_count,source_ids,metadata_json,algorithm_version,
                    owner_type,authority,sensitivity,evidence_json,created_by,
                    effective_from,created_at,updated_at)
                   VALUES (?, 'experience', ?, ?, ?, ?, ?, ?, 0.6, ?, ?, ?,
                           ?, ?, ?, ?,'bot','bot_observation',
                           'group', ?, ?, ?, ?, ?)
                   ON CONFLICT(record_id) DO UPDATE SET content=excluded.content,
                     status=excluded.status,
                     confidence=excluded.confidence,
                     supporting_count=excluded.supporting_count,
                     source_ids=excluded.source_ids,updated_at=excluded.updated_at""",
                (record_id, group_id, case[0], next_status, content, case[2] or "", target_confidence, case[8] or "",
                 environment["signature"], failure_signature, len(source_ids), json.dumps(source_ids), metadata,
                algorithm_version, safe_memory_mapping(correction), f"service:experience_distiller", now, now, now),
            )
            if self._projection_outbox is not None:
                await self._projection_outbox.enqueue(
                    db,
                    event_id=f"experience-vector:{record_id}",
                    projection_type="experience_vector_upsert",
                    aggregate_id=record_id,
                    aggregate_version=signature,
                    group_id=group_id,
                    payload={
                        "record_id": record_id,
                        "content": content,
                        "group_id": group_id,
                        "bot_id": case[0],
                        "confidence": 0.73,
                        "timestamp": now / 1000,
                    },
                    now_ms=now,
                )
            await db.commit()
        skill_job = await self._jobs.enqueue(
            MemoryScope.group(group_id=group_id, actor_id="service:experience_distiller"),
            "compile_skill_candidate", record_id, input_version,
        )
        if len(source_ids) >= 2:
            async with await self._database.connect("pipeline_jobs", group_id, write=True) as db:
                await db.execute(
                    """UPDATE pipeline_jobs
                       SET status='pending',attempt=0,lease_until=NULL,
                           lease_token=NULL,error='',output_json='{}',
                           completed_at=NULL,updated_at=?
                       WHERE job_id=? AND status='completed'""",
                    (now, skill_job),
                )
                await db.commit()
        return {"case_id": case_id, "record_id": record_id, "distilled": True,
                "skill_job_id": skill_job, "promotion_required": False}
