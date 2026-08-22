"""Canonical Learning application service.

The service owns the LearningPort runtime path.  It reads and writes the
canonical group database directly and never calls ``backend.ai`` modules.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict
from typing import Any

from memory.application.references import experience_ref, skill_ref
from memory.contracts import (
    ApproveSkillCandidate, AssembleCase, CompleteExperienceUsage,
    CompleteSkillUsage, ListSkillCandidates, MarkUsageAdopted,
    MarkUsageExecuted, ProcessLearningCase, RecallExperiences, RecallSkills,
    ResolveLearningRefs, SkillCandidate, VerifyUsage, MemoryOperationError,
)
from memory.domain import (
    MemoryScope, ScopeKind, UsageKind, OutcomeStatus,
    UsageState, evaluate_outcome_signal, evaluate_outcome_verdict, identify_task,
    require_adoption_evidence, require_execution_evidence,
    require_verification_evidence,
)
from memory.domain.safety import safe_memory_mapping, safe_memory_text
from memory.application.context import require_pipeline_repository
from memory.ports import LearningPort, MemoryDatabasePort, PipelineJobRepositoryPort


class CanonicalLearningService(LearningPort):
    def __init__(
        self,
        database: MemoryDatabasePort,
        job_repository: PipelineJobRepositoryPort | None = None,
    ) -> None:
        self._database = database
        self._job_repository = job_repository or require_pipeline_repository(self._database)

    @property
    def job_repository(self) -> PipelineJobRepositoryPort:
        """Expose canonical durable job mechanics to the runtime boundary."""
        return self._job_repository

    async def job_stats(self, group_id: int) -> dict[str, int]:
        return await self._job_repository.stats(
            MemoryScope.group(group_id=group_id, actor_id="service:learning_stats")
        )

    async def recall_experiences(self, command: RecallExperiences) -> tuple[str, list[str]]:
        group_id, bot_id = _scope(command.scope)
        terms = _terms(command.query)
        async with await self._database.connect("memory_records", group_id, write=False) as db:
            async with db.execute(
                """SELECT record_id,content,confidence,semantic_cluster_key FROM memory_records
                   WHERE group_id=? AND bot_id=? AND kind='experience' AND status='active'
                   ORDER BY confidence DESC,updated_at DESC LIMIT 500""",
                (group_id, bot_id),
            ) as cur:
                rows = await cur.fetchall()
        ranked = []
        for row in rows:
            overlap = len(terms & _terms(str(row[1])))
            if terms and overlap == 0:
                continue
            ranked.append((overlap / max(1, len(terms)) * 0.7 + float(row[2]) * 0.3, row))
        ranked.sort(key=lambda item: (item[0], item[1][0]), reverse=True)
        body, ids, used = [], [], 0
        for score, row in ranked[:command.limit]:
            content = safe_memory_text(row[1], limit=1800)
            block = f'<untrusted_learned_experience memory_ref="{row[0]}">\n{content}\n</untrusted_learned_experience>'
            if used + len(block) > command.char_budget:
                break
            used += len(block)
            body.append(block)
            ids.append(str(row[0]))
        if ids:
            now = int(time.time() * 1000)
            async with await self._database.connect("experience_usage", group_id, write=True) as db:
                for record_id in ids:
                    await db.execute(
                        """INSERT INTO experience_usage
                           (record_id,run_id,group_id,bot_id,state,created_at,updated_at)
                           VALUES (?,?,?,?,?,?,?)
                           ON CONFLICT(record_id,run_id) DO UPDATE SET updated_at=excluded.updated_at
                           WHERE experience_usage.state='injected'""",
                        (record_id, command.run_id, group_id, bot_id, "injected", now, now),
                    )
                await db.commit()
        return ("[Relevant prior execution experience]\n" + "\n".join(body), ids) if body else ("", [])

    async def decay_experiences(self, group_id: int, *, now_ms: int | None = None, stale_days: int = 90) -> int:
        now = now_ms or int(time.time() * 1000)
        cutoff = now - stale_days * 86_400_000
        async with await self._database.connect("memory_records", group_id, write=True) as db:
            cur = await db.execute(
                """UPDATE memory_records SET status='deprecated',valid_to=?,updated_at=?
                   WHERE group_id=? AND kind='experience' AND status='active'
                     AND confidence<0.5 AND COALESCE(last_used_at,created_at)<?""",
                (now, now, group_id, cutoff),
            )
            await db.commit()
        return cur.rowcount

    async def recall_skills(self, command: RecallSkills) -> tuple[str, list[str]]:
        group_id, bot_id = _scope(command.scope)
        terms = _terms(command.query)
        if not terms:
            return "", []
        async with await self._database.connect("skills", group_id, write=False) as db:
            async with db.execute(
                """SELECT s.skill_id,s.current_version,s.maturity,v.declaration_json
                   FROM skills s JOIN skill_versions v ON v.skill_id=s.skill_id AND v.version=s.current_version
                   WHERE s.group_id=? AND (s.bot_id=? OR s.bot_id IS NULL) AND s.status='active'
                     AND s.maturity IN ('trial','active','stable')""",
                (group_id, bot_id),
            ) as cur:
                rows = await cur.fetchall()
        weights = {"stable": 1.0, "active": 0.9, "trial": 0.7}
        ranked = []
        for skill_id, version, maturity, raw in rows:
            declaration = json.loads(raw or "{}")
            text = f"{declaration.get('name', '')} {declaration.get('trigger', '')}"
            lexical = len(terms & _terms(text)) / max(1, len(terms | _terms(text)))
            if lexical >= 0.08:
                ranked.append((lexical * weights.get(str(maturity), 0.7), str(skill_id), int(version), declaration))
        ranked.sort(reverse=True)
        selected = ranked[:command.limit]
        now = int(time.time() * 1000)
        if selected:
            async with await self._database.connect("skill_usage", group_id, write=True) as db:
                for _, skill_id, version, _ in selected:
                    await db.execute(
                        "INSERT INTO skill_usage(skill_id,version,run_id,group_id,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(skill_id,run_id) DO NOTHING",
                        (skill_id, version, command.run_id, group_id, now, now),
                    )
                await db.commit()
        body = []
        ids = []
        for _, skill_id, version, declaration in selected:
            trigger = safe_memory_text(declaration.get("trigger", ""), limit=500)
            procedure = "; ".join(safe_memory_text(step, limit=500) for step in declaration.get("procedure", []))
            body.append(f'<untrusted_learned_skill memory_ref="{skill_ref(skill_id, version)}">\nTrigger pattern: "{trigger}"\nProcedure: {procedure}\n</untrusted_learned_skill>')
            ids.append(skill_id)
        return ("[Learned declarative skills]\n" + "\n".join(body), ids) if body else ("", [])

    async def complete_experience_usage(self, command: CompleteExperienceUsage) -> None:
        await self.record_completion_telemetry(type("Completion", (), {
            "scope": command.scope, "kind": UsageKind.EXPERIENCE,
            "item_ids": command.record_ids, "run_id": command.run_id,
            "outcome": command.outcome, "input_tokens": command.input_tokens,
            "output_tokens": command.output_tokens, "tool_attempts": command.tool_attempts,
        })())

    async def complete_skill_usage(self, command: CompleteSkillUsage) -> None:
        await self.record_completion_telemetry(type("Completion", (), {
            "scope": command.scope, "kind": UsageKind.SKILL,
            "item_ids": command.skill_ids, "run_id": command.run_id,
            "outcome": command.outcome, "input_tokens": 0,
            "output_tokens": 0, "tool_attempts": 0,
        })())

    async def record_completion_telemetry(self, command: Any) -> int:
        """Record terminal run telemetry without asserting Memory adoption."""
        group_id, _ = _scope(command.scope)
        kind = command.kind if isinstance(command.kind, UsageKind) else UsageKind(str(command.kind))
        table = "experience_usage" if kind is UsageKind.EXPERIENCE else "skill_usage"
        id_column = "record_id" if table == "experience_usage" else "skill_id"
        changed = 0
        async with await self._database.connect(table, group_id, write=True) as db:
            for item_id in command.item_ids:
                if kind is UsageKind.EXPERIENCE:
                    cur = await db.execute(
                        f"UPDATE {table} SET outcome=?,input_tokens=?,output_tokens=?,tool_attempts=?,updated_at=? WHERE {id_column}=? AND run_id=? AND group_id=?",
                        (safe_memory_text(command.outcome, limit=500), command.input_tokens, command.output_tokens,
                         command.tool_attempts, int(time.time()*1000), item_id,
                         command.run_id, group_id),
                    )
                else:
                    cur = await db.execute(
                        f"UPDATE {table} SET outcome=?,updated_at=? WHERE {id_column}=? AND run_id=? AND group_id=?",
                        (safe_memory_text(command.outcome, limit=500), int(time.time()*1000), item_id,
                         command.run_id, group_id),
                    )
                changed += cur.rowcount
            await db.commit()
        return changed

    async def resolve_learning_refs(self, command: ResolveLearningRefs) -> tuple[str, ...]:
        group_id, bot_id = _scope(command.scope)
        refs = tuple(experience_ref(item) for item in command.experience_ids if item.startswith("exp:"))
        if command.skill_ids:
            placeholders = ",".join("?" for _ in command.skill_ids)
            async with await self._database.connect("skills", group_id, write=False) as db:
                async with db.execute(f"SELECT skill_id,current_version FROM skills WHERE group_id=? AND bot_id=? AND status='active' AND maturity IN ('active','stable') AND skill_id IN ({placeholders})", (group_id, bot_id, *command.skill_ids)) as cur:
                    versions = {str(row[0]): int(row[1]) for row in await cur.fetchall()}
            refs += tuple(skill_ref(item, versions[item]) for item in command.skill_ids if item in versions)
        return tuple(sorted(refs))

    async def list_skill_candidates(self, command: ListSkillCandidates) -> tuple[SkillCandidate, ...]:
        group_id, bot_id = _scope(command.scope)
        async with await self._database.connect("skills", group_id, write=False) as db:
            async with db.execute("SELECT s.skill_id,s.name,s.maturity,s.risk_level,s.current_version,s.success_count,s.failure_count,v.declaration_json,v.evidence_ids FROM skills s JOIN skill_versions v ON v.skill_id=s.skill_id AND v.version=s.current_version WHERE s.group_id=? AND s.bot_id=? ORDER BY s.updated_at DESC", (group_id, bot_id)) as cur:
                rows = await cur.fetchall()
        return tuple(SkillCandidate(skill_id=str(r[0]), name=str(r[1]), maturity=str(r[2]), risk_level=str(r[3]), version=int(r[4]), success_count=int(r[5]), failure_count=int(r[6]), declaration=json.loads(r[7] or "{}"), evidence_ids=tuple(json.loads(r[8] or "[]"))) for r in rows)

    async def approve_skill_candidate(self, command: ApproveSkillCandidate) -> bool:
        group_id, bot_id = _scope(command.scope)
        if command.scope.user_id is None:
            raise MemoryOperationError("Skill approval requires authenticated user scope")
        async with await self._database.connect("skills", group_id, write=True) as db:
            cur = await db.execute("UPDATE skills SET maturity='active',updated_at=? WHERE skill_id=? AND group_id=? AND bot_id=?", (int(time.time()*1000), command.skill_id, group_id, bot_id))
            await db.commit()
        return cur.rowcount == 1

    async def promote_skill(self, *, skill_id: str, group_id: int, target_maturity: str,
                            bot_id: int | None, actor_id: str, reason: str) -> bool:
        if target_maturity not in {"active", "stable"}:
            raise ValueError("Invalid target maturity for promotion")
        if not actor_id.startswith("user:") or not actor_id[5:].isdigit():
            raise ValueError("Promotion requires a human user actor_id")
        if not reason.strip():
            raise ValueError("Promotion reason is required")
        previous = "trial" if target_maturity == "active" else "active"
        now = int(time.time() * 1000)
        async with await self._database.connect("skills", group_id, write=True) as db:
            sql = "UPDATE skills SET maturity=?,updated_at=? WHERE skill_id=? AND group_id=? AND status='active' AND maturity=?"
            params: tuple[Any, ...] = (target_maturity, now, skill_id, group_id, previous)
            if bot_id is not None:
                sql += " AND bot_id=?"
                params += (bot_id,)
            cur = await db.execute(sql, params)
            if cur.rowcount:
                await db.execute(
                    """INSERT INTO skill_promotion_audit
                       (skill_id,group_id,actor_id,reason,from_maturity,to_maturity,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (skill_id, group_id, actor_id, reason, previous, target_maturity, now),
                )
            await db.commit()
        if cur.rowcount == 1:
            await self._job_repository.enqueue(
                MemoryScope.group(group_id=group_id, actor_id="service:skill_promotion"),
                "project_skill", skill_id, f"1:{target_maturity}:active",
            )
        return cur.rowcount == 1

    async def mark_usage_adopted(self, command: MarkUsageAdopted) -> int:
        require_adoption_evidence(command.adopted_via, command.evidence)
        return await self._usage_update(command, "adopted", "adopted_at", command.evidence)

    async def mark_usage_executed(self, command: MarkUsageExecuted) -> int:
        require_execution_evidence(command.evidence)
        return await self._usage_update(command, "executed", "executed_at", command.evidence)

    async def verify_usage(self, command: VerifyUsage) -> int:
        require_verification_evidence(command.status, command.evidence)
        return await self._usage_update(command, str(command.status.value), "verified_at", command.evidence)

    async def _usage_update(self, command: Any, state: str, timestamp_column: str, evidence: Any) -> int:
        group_id, _ = _scope(command.scope)
        kind = command.kind
        if not isinstance(kind, UsageKind):
            kind = UsageKind(str(kind))
        table = "experience_usage" if kind is UsageKind.EXPERIENCE else "skill_usage"
        id_column = "record_id" if table == "experience_usage" else "skill_id"
        evidence_column = "adoption_evidence_json" if state == "adopted" else "execution_evidence_json" if state == "executed" else "verification_evidence_json"
        extra_column = "adopted_via" if state == "adopted" else "verification_status" if state.startswith("verified_") else None
        changed = 0
        async with await self._database.connect(table, group_id, write=True) as db:
            for item_id in command.item_ids:
                if state.startswith("verified_"):
                    # Verification is the only transition that reinforces a
                    # memory.  Read the current state first so retries are
                    # explicit no-ops and reinforcement remains idempotent.
                    async with db.execute(
                        f"SELECT state,verification_status FROM {table} WHERE {id_column}=? AND run_id=? AND group_id=?",
                        (item_id, command.run_id, group_id),
                    ) as current_cursor:
                        current = await current_cursor.fetchone()
                    if current is None or current[0] != "executed":
                        continue
                    cur = await db.execute(
                        f"UPDATE {table} SET state=?,verification_status=?,verified_at=?,verification_evidence_json=?,updated_at=? WHERE {id_column}=? AND run_id=? AND group_id=? AND state='executed'",
                        (state, state, int(time.time()*1000), safe_memory_mapping(evidence),
                         int(time.time()*1000), item_id, command.run_id, group_id),
                    )
                    changed += cur.rowcount
                    if cur.rowcount:
                        if kind is UsageKind.EXPERIENCE and state == "verified_success":
                            await db.execute("UPDATE memory_records SET supporting_count=supporting_count+1,confidence=MIN(0.98,confidence+0.03),last_used_at=?,updated_at=? WHERE record_id=? AND group_id=?", (int(time.time()*1000), int(time.time()*1000), item_id, group_id))
                        elif kind is UsageKind.EXPERIENCE:
                            await db.execute("UPDATE memory_records SET contradicting_count=contradicting_count+1,confidence=MAX(0.05,confidence-0.12),last_used_at=?,updated_at=?,status=CASE WHEN contradicting_count+1>=2 THEN 'suspended' ELSE status END WHERE record_id=? AND group_id=?", (int(time.time()*1000), int(time.time()*1000), item_id, group_id))
                        elif state == "verified_success":
                            await db.execute(
                                "UPDATE skills SET success_count=success_count+1,updated_at=? WHERE skill_id=? AND group_id=?",
                                (int(time.time()*1000), item_id, group_id),
                            )
                        else:
                            await db.execute(
                                "UPDATE skills SET failure_count=failure_count+1,status=CASE WHEN failure_count+1>=2 THEN 'suspended' ELSE status END,updated_at=? WHERE skill_id=? AND group_id=?",
                                (int(time.time()*1000), item_id, group_id),
                            )
                    continue
                extra_sql = f",{extra_column}=?" if extra_column else ""
                extra_value = (
                    command.adopted_via if state == "adopted"
                    else state if extra_column == "verification_status" else None
                )
                params = [state, int(time.time()*1000), safe_memory_mapping(evidence)]
                if extra_column:
                    params.append(extra_value)
                params.extend([int(time.time()*1000), item_id, command.run_id, group_id])
                predecessor = "injected" if state == "adopted" else "adopted" if state == "executed" else None
                transition_guard = f" AND state='{predecessor}'" if predecessor else ""
                cur = await db.execute(f"UPDATE {table} SET state=?,{timestamp_column}=?,{evidence_column}=?{extra_sql},updated_at=? WHERE {id_column}=? AND run_id=? AND group_id=?{transition_guard}", params)
                changed += cur.rowcount
                if cur.rowcount and state.startswith("verified_"):
                    if kind is UsageKind.EXPERIENCE:
                        if state == "verified_success":
                            await db.execute(
                                "UPDATE memory_records SET supporting_count=supporting_count+1,confidence=MIN(0.98,confidence+0.03),last_used_at=?,updated_at=? WHERE record_id=? AND group_id=?",
                                (int(time.time()*1000), int(time.time()*1000), item_id, group_id),
                            )
                        else:
                            await db.execute(
                                "UPDATE memory_records SET contradicting_count=contradicting_count+1,confidence=MAX(0.05,confidence-0.12),last_used_at=?,updated_at=?,status=CASE WHEN contradicting_count+1>=2 THEN 'suspended' ELSE status END WHERE record_id=? AND group_id=?",
                                (int(time.time()*1000), int(time.time()*1000), item_id, group_id),
                            )
                    elif state == "verified_success":
                        await db.execute(
                            "UPDATE skills SET success_count=success_count+1,updated_at=? WHERE skill_id=? AND group_id=?",
                            (int(time.time()*1000), item_id, group_id),
                        )
                    else:
                        await db.execute(
                            "UPDATE skills SET failure_count=failure_count+1,status=CASE WHEN failure_count+1>=2 THEN 'suspended' ELSE status END,updated_at=? WHERE skill_id=? AND group_id=?",
                            (int(time.time()*1000), item_id, group_id),
                        )
            await db.commit()
        return changed

    async def assemble_case(self, command: AssembleCase) -> str | None:
        group_id, bot_id = _scope(command.scope)
        if group_id is None or not command.run_id:
            return None
        tools, files, errors = [], [], []
        for record in command.tool_records:
            name = str(record.get("name") or "")
            if name and name not in tools:
                tools.append(name)
            args = record.get("args") or {}
            for key in ("path", "file_path"):
                value = args.get(key) if isinstance(args, dict) else None
                if value:
                    safe_value = safe_memory_text(str(value), limit=500)
                    if safe_value not in files:
                        files.append(safe_value)
            if record.get("is_error"):
                errors.append(safe_memory_text(str(record.get("result") or ""), limit=1000))
        verdict = evaluate_outcome_verdict(
            terminal_outcome=command.outcome, tool_records=command.tool_records
        )
        correction: dict[str, Any] = {}
        if verdict.correction is not None:
            correction = asdict(verdict.correction)
            correction["corrective_actions"] = [
                {
                    "adapter": verdict.signals[index].adapter,
                    "target": verdict.signals[index].target,
                    "evidence": dict(verdict.signals[index].evidence),
                }
                for index in verdict.correction.corrective_signal_indices
            ]
        elif errors:
            correction = {
                "source": "autogen_task_centric_failure_insight",
                "category": "execution_error",
                "insight_summary": safe_memory_text(errors[0], limit=500),
                "corrective_action": "review the failed execution trace before retrying",
                "relevancy_score": 0.5,
                "corrective_actions": [],
            }
        signals = [verdict.status.value]
        signals.extend(
            f"{signal.adapter}:{'success' if signal.success else 'failure'}"
            for signal in verdict.signals
        )
        if correction:
            signals.append("corrected_success")
        try:
            correction = json.loads(safe_memory_mapping(correction, limit=16_000))
        except (TypeError, ValueError, json.JSONDecodeError):
            correction = {"source": "canonical_safety_fallback"}
        generated_case_id = "case:" + hashlib.sha256(f"{group_id}:{command.run_id}".encode()).hexdigest()[:24]
        now = int(time.time()*1000)
        identity = identify_task(command.task)
        attempt_trace = []
        saw_verifier_failure = False
        investigate_tools = {"read_file", "list_files", "search_files", "search_code", "web_search"}
        for ordinal, record in enumerate(command.tool_records):
            signal = evaluate_outcome_signal(record)
            name = str(record.get("name") or "")
            if signal is not None and signal.verifies_task:
                phase = "verify"
            elif name in investigate_tools:
                phase = "investigate"
            elif signal is not None and signal.adapter == "file_change" and saw_verifier_failure:
                phase = "recover"
            else:
                phase = "execute"
            args = record.get("args") if isinstance(record.get("args"), dict) else {}
            target = signal.target if signal is not None and signal.adapter != "shell_exit" else str(args.get("path") or args.get("file_path") or args.get("url") or "")
            summary = safe_memory_text(str(record.get("result") or ""), limit=500).replace("\n", " ")
            attempt_trace.append((
                ordinal, str(record.get("step_id") or f"{command.run_id}:step:{ordinal + 1}"),
                str(record.get("attempt_id") or f"{command.run_id}:attempt:{ordinal + 1}"),
                phase, name, safe_memory_text(target, limit=500),
                "error" if record.get("is_error") else "success", summary,
                signal.adapter if signal is not None and signal.verifies_task else "",
                int(signal is not None and signal.verifies_task),
            ))
            if signal is not None and signal.verifies_task and not signal.success:
                saw_verifier_failure = True
        async with await self._database.connect("agent_cases", group_id, write=True) as db:
            async with db.execute(
                "SELECT case_id FROM agent_cases WHERE run_id=? AND group_id=?",
                (command.run_id, group_id),
            ) as existing_cursor:
                existing = await existing_cursor.fetchone()
            case_id = str(existing[0]) if existing else generated_case_id
            await db.execute("""INSERT INTO agent_cases
              (case_id,run_id,group_id,bot_id,task,task_signature,semantic_cluster_key,
               task_family,task_concepts_json,tools_used,files_touched,attempts,errors,
               outcome,outcome_confidence,outcome_status,verification_adapter,
               correction_evidence_json,verification_signals,summary,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(group_id,run_id) DO UPDATE SET task=excluded.task,task_signature=excluded.task_signature,
               semantic_cluster_key=excluded.semantic_cluster_key,task_family=excluded.task_family,
               task_concepts_json=excluded.task_concepts_json,tools_used=excluded.tools_used,
               files_touched=excluded.files_touched,attempts=excluded.attempts,errors=excluded.errors,
               outcome=excluded.outcome,outcome_confidence=excluded.outcome_confidence,
               outcome_status=excluded.outcome_status,verification_adapter=excluded.verification_adapter,
               correction_evidence_json=excluded.correction_evidence_json,
               verification_signals=excluded.verification_signals,summary=excluded.summary,updated_at=excluded.updated_at""",
              (case_id, command.run_id, group_id, bot_id, safe_memory_text(command.task, limit=4000),
               identity.exact_signature, identity.semantic_cluster_key, identity.family,
               json.dumps(identity.concepts), json.dumps(tools), json.dumps(files),
               len(command.tool_records), json.dumps(errors), command.outcome, verdict.confidence,
               verdict.status.value, verdict.primary_adapter, json.dumps(correction),
               json.dumps(signals), f"{verdict.status.value}; {len(command.tool_records)} tool attempts; {len(errors)} errors", now, now))
            await db.execute("DELETE FROM agent_case_attempts WHERE case_id=? AND group_id=?", (case_id, group_id))
            for attempt in attempt_trace:
                await db.execute("""INSERT INTO agent_case_attempts
                  (case_id,ordinal,group_id,bot_id,step_id,attempt_id,phase,action_tool,
                   action_target,observation_status,observation_summary,verifier_adapter,
                   verifies_task,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (case_id, attempt[0], group_id, bot_id, *attempt[1:], now))
            await db.commit()
        return case_id

    async def process_case(self, command: ProcessLearningCase) -> str:
        group_id, _ = _scope(command.scope)
        return await self._job_repository.enqueue(
            MemoryScope.group(group_id=group_id, actor_id="service:learning"),
            "process_case", command.case_id, command.input_version,
        )

    async def repair_observation_gaps(self, group_id: int, *, limit: int = 100) -> int:
        """Requeue committed bot messages that have no observation job."""
        async with await self._database.connect("messages", group_id, write=False) as db:
            async with db.execute("PRAGMA table_info(messages)") as columns_cursor:
                columns = {str(row[1]) for row in await columns_cursor.fetchall()}
            sender_filter = "AND m.sender_type='bot'" if "sender_type" in columns else ""
            async with db.execute(
                f"""SELECT m.id,m.member_id FROM messages m
                   LEFT JOIN pipeline_jobs p ON p.group_id=m.group_id
                     AND p.job_type='observe_turn'
                     AND p.input_id=CAST(m.id AS TEXT)||':'||CAST(m.member_id AS TEXT)
                     AND p.input_version='1'
                   WHERE m.group_id=? AND m.is_deleted=0 AND p.job_id IS NULL {sender_filter}
                   ORDER BY m.id LIMIT ?""",
                (group_id, max(1, limit)),
            ) as cur:
                rows = await cur.fetchall()
        repaired = 0
        scope = MemoryScope.group(group_id=group_id, actor_id="service:learning_repair")
        for message_id, bot_id in rows:
            await self._job_repository.enqueue(
                scope, "observe_turn", f"{message_id}:{bot_id}", "1"
            )
            repaired += 1
        return repaired

    async def repair_skill_projection_gaps(self, group_id: int) -> int:
        """Requeue canonical skills without a completed projection job."""
        async with await self._database.connect("skills", group_id, write=False) as db:
            async with db.execute(
                """SELECT s.skill_id,s.current_version FROM skills s
                   WHERE s.group_id=? AND s.status='active'
                     AND NOT EXISTS (
                       SELECT 1 FROM pipeline_jobs p WHERE p.group_id=s.group_id
                         AND p.job_type='project_skill' AND p.input_id=s.skill_id
                         AND p.input_version=CAST(s.current_version AS TEXT)
                         AND p.status='completed')""",
                (group_id,),
            ) as cur:
                rows = await cur.fetchall()
        repaired = 0
        scope = MemoryScope.group(group_id=group_id, actor_id="service:learning_repair")
        for skill_id, version in rows:
            await self._job_repository.enqueue(
                scope, "project_skill", str(skill_id), str(version)
            )
            repaired += 1
        return repaired


def _scope(scope: MemoryScope) -> tuple[int, int | None]:
    if scope.kind not in (ScopeKind.GROUP, ScopeKind.BOT) or scope.group_id is None:
        raise MemoryOperationError("learning operation requires group scope")
    return scope.group_id, scope.bot_id


def _terms(value: str) -> set[str]:
    lower = str(value or "").lower()
    terms = set(re.findall(r"[a-z0-9_]{2,}", lower))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lower))
    terms.update(chinese[i:i+2] for i in range(max(0, len(chinese)-1)))
    return terms
