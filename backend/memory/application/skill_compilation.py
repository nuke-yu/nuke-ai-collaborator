"""Canonical Experience -> declarative Skill compilation."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from memory.application.pipeline import CanonicalPipelineJobRepository
from memory.application.context import require_database
from memory.domain import MemoryScope
from memory.domain.safety import safe_memory_mapping, safe_memory_text
from memory.ports import MemoryDatabasePort


class CanonicalSkillCompiler:
    def __init__(self, database: MemoryDatabasePort | None = None) -> None:
        self._database = database or require_database()
        self._jobs = CanonicalPipelineJobRepository(self._database)

    async def compile(self, group_id: int, record_id: str, input_version: str = "1") -> dict[str, Any]:
        async with await self._database.connect("memory_records", group_id, write=False) as db:
            async with db.execute(
                """SELECT bot_id,content,task_signature,confidence,supporting_count,
                          source_ids FROM memory_records
                   WHERE record_id=? AND group_id=? AND kind='experience'
                     AND status='active'""", (record_id, group_id)
            ) as cur:
                row = await cur.fetchone()
        if not row or float(row[3] or 0) < 0.7 or int(row[4] or 0) < 2:
            return {"record_id": record_id, "skill_id": None, "compiled": False,
                    "promotion_required": False, "skill_promoted": False}
        try:
            experience = json.loads(row[1] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"record_id": record_id, "skill_id": None, "compiled": False,
                    "promotion_required": False, "skill_promoted": False}
        trigger = safe_memory_text(experience.get("task_pattern", ""), limit=500)
        if not trigger:
            return {"record_id": record_id, "skill_id": None, "compiled": False,
                    "promotion_required": False, "skill_promoted": False}
        source_ids = json.loads(row[5] or "[]") if row[5] else []
        declaration = {
            "risk_level": "S0",
            "trigger": trigger,
            "procedure": [
                "Review the prior failure mode before planning",
                "Apply the verified corrective lesson",
            ],
            "verification": [experience.get("verification", {}).get("adapter", "")],
            "limitations": ["revalidate_when_environment_signature_changes"],
            "allowed_tools": [],
            "provenance": {"source_case_ids": list(source_ids), "record_id": record_id},
        }
        declaration["execution_plan"] = {
            "trigger": trigger,
            "steps": list(declaration["procedure"]),
            "allowed_tools": [],
            "verification": list(declaration["verification"]),
            "requires_hil": False,
        }
        canonical = safe_memory_mapping(declaration, limit=100_000)
        skill_id = "skill:" + hashlib.sha256(
            f"{group_id}:{row[0]}:{row[2]}".encode()
        ).hexdigest()[:24]
        name = "learned-" + _safe_slug(trigger)
        now = int(time.time() * 1000)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        async with await self._database.connect("skills", group_id, write=True) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS everos_source_documents (
                   source_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
                   record_id TEXT NOT NULL, source_type TEXT NOT NULL,
                   content_json TEXT NOT NULL, created_at INTEGER NOT NULL)"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS everos_source_markdown (
                   source_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
                   record_id TEXT NOT NULL, markdown TEXT NOT NULL,
                   created_at INTEGER NOT NULL)"""
            )
            source_id = "everos-source:" + hashlib.sha256(
                f"{group_id}:{record_id}".encode()
            ).hexdigest()[:24]
            await db.execute(
                """INSERT OR REPLACE INTO everos_source_documents
                   (source_id,group_id,record_id,source_type,content_json,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (source_id, group_id, record_id, "experience_case_snapshot", canonical, now),
            )
            await db.execute(
                """INSERT OR REPLACE INTO everos_source_markdown
                   (source_id,group_id,record_id,markdown,created_at)
                   VALUES (?,?,?,?,?)""",
                (source_id, group_id, record_id,
                 f"# Experience Source: {record_id}\n\n## Task Pattern\n{trigger}\n\n## Source Cases\n" +
                 "\n".join(f"- {case_id}" for case_id in source_ids), now),
            )
            await db.execute(
                """INSERT INTO skills
                   (skill_id,group_id,bot_id,name,maturity,risk_level,current_version,
                    status,created_at,updated_at)
                   VALUES (?, ?, ?, ?, 'trial', 'S0', 1, 'active', ?, ?)
                   ON CONFLICT(skill_id) DO UPDATE SET updated_at=excluded.updated_at""",
                (skill_id, group_id, row[0], name, now, now),
            )
            await db.execute(
                """INSERT INTO skill_versions
                   (skill_id,version,declaration_json,content_hash,evidence_ids,created_at)
                   VALUES (?,1,?,?,?,?) ON CONFLICT(skill_id,version) DO NOTHING""",
                (skill_id, canonical, digest, json.dumps(source_ids), now),
            )
            await db.commit()
        project_job = await self._jobs.enqueue(
            MemoryScope.group(group_id=group_id, actor_id="service:skill_compiler"),
            "project_skill", skill_id, "1:trial:active",
        )
        return {"record_id": record_id, "skill_id": skill_id, "compiled": True,
                "project_job_id": project_job, "input_version": input_version,
                "promotion_required": True, "skill_promoted": False}


def _safe_slug(value: str) -> str:
    result = "-".join("".join(char if char.isalnum() else " " for char in value).split())
    return (result.lower() or "experience")[:60]
