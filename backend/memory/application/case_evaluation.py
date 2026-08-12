"""Canonical, deterministic Case outcome gate."""
from __future__ import annotations

import json
from typing import Any

from memory.infrastructure import SQLiteMemoryDatabase


class CanonicalCaseEvaluator:
    def __init__(self, database: SQLiteMemoryDatabase | None = None) -> None:
        self._database = database or SQLiteMemoryDatabase()

    async def evaluate(self, group_id: int, case_id: str) -> dict[str, Any]:
        async with await self._database.connect("agent_cases", group_id, write=False) as db:
            async with db.execute(
                """SELECT outcome,errors,attempts,outcome_status,correction_evidence_json
                   FROM agent_cases WHERE case_id=? AND group_id=?""",
                (case_id, group_id),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            raise ValueError(f"case not found: {case_id}")
        try:
            errors = json.loads(row[1] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            errors = []
        try:
            correction = json.loads(row[4] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            correction = {}
        if row[0] != "completed" or row[3] == "verified_failure":
            classification, gain, should, confidence = "failed", "high", False, 0.9
        elif row[3] == "verified_success" and correction:
            classification, gain, should, confidence = "corrected_success", "high", True, 0.9
        elif row[3] == "verified_success":
            classification, gain, should, confidence = "ordinary_success", "low", False, 1.0
        else:
            classification, gain, should, confidence = "unverified_completion", "low", False, 0.5
        return {
            "classification": classification,
            "information_gain": gain,
            "should_distill": should,
            "confidence": confidence,
            "has_errors": bool(errors),
        }
