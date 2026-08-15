"""Read-only shadow metrics for the evidence-gated learning rollout."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from memory.application.context import require_database
from memory.ports import MemoryDatabasePort

def _database() -> MemoryDatabasePort:
    return require_database()


@dataclass(frozen=True, slots=True)
class LearningShadowMetrics:
    experience_completion_without_adoption: int = 0
    skill_completion_without_adoption: int = 0
    experience_verified_success: int = 0
    experience_verified_failure: int = 0
    skill_verified_success: int = 0
    skill_verified_failure: int = 0
    cases_unverified_completion: int = 0
    cases_verified_success: int = 0
    cases_verified_failure: int = 0
    cases_corrected_success: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


async def collect_learning_shadow_metrics(
    group_id: int | None,
) -> LearningShadowMetrics:
    if group_id is None:
        return LearningShadowMetrics()
    async with await _database().connect("experience_usage", group_id, write=False) as db:
        experience = await _usage_counts(db, "experience_usage")
        skill = await _usage_counts(db, "skill_usage")
        async with db.execute(
            """SELECT
                SUM(CASE WHEN outcome_status='unverified_completion' THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome_status='verified_success' THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome_status='verified_failure' THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome_status='verified_success'
                    AND correction_evidence_json!='{}' THEN 1 ELSE 0 END)
                FROM agent_cases WHERE group_id=?""",
            (group_id,),
        ) as cursor:
            case_row = await cursor.fetchone()
    cases = tuple(int(value or 0) for value in case_row)
    return LearningShadowMetrics(
        experience_completion_without_adoption=experience[0],
        skill_completion_without_adoption=skill[0],
        experience_verified_success=experience[1],
        experience_verified_failure=experience[2],
        skill_verified_success=skill[1],
        skill_verified_failure=skill[2],
        cases_unverified_completion=cases[0],
        cases_verified_success=cases[1],
        cases_verified_failure=cases[2],
        cases_corrected_success=cases[3],
    )


async def _usage_counts(db, table: str) -> tuple[int, int, int]:
    async with db.execute(
        f"""SELECT
            SUM(CASE WHEN outcome='completed' AND state='injected'
                THEN 1 ELSE 0 END),
            SUM(CASE WHEN state='verified_success' THEN 1 ELSE 0 END),
            SUM(CASE WHEN state='verified_failure' THEN 1 ELSE 0 END)
            FROM {table}"""
    ) as cursor:
        row = await cursor.fetchone()
    return tuple(int(value or 0) for value in row)
