"""Causal usage tracking shared by Experiences and learned Skills."""
from __future__ import annotations

import json
import time
from typing import Any, Mapping, Sequence

from memory.domain import (
    UsageKind,
    UsageState,
    require_adoption_evidence,
    require_execution_evidence,
    require_verification_evidence,
)

_USAGE_STORAGE = {
    UsageKind.EXPERIENCE: ("experience_usage", "record_id"),
    UsageKind.SKILL: ("skill_usage", "skill_id"),
}


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def mark_adopted(
    *,
    kind: UsageKind,
    item_ids: Sequence[str],
    run_id: str,
    group_id: int | None,
    adopted_via: str,
    evidence: Mapping[str, Any],
) -> int:
    if group_id is None or not item_ids:
        return 0
    require_adoption_evidence(adopted_via, evidence)
    table, id_column = _USAGE_STORAGE[kind]
    from ai.memory import _memory_db

    now = int(time.time() * 1000)
    changed = 0
    async with await _memory_db(table, group_id, write=True) as db:
        for item_id in item_ids:
            cursor = await db.execute(
                f"""UPDATE {table}
                SET state='adopted',adopted_at=?,adopted_via=?,
                    adoption_evidence_json=?,updated_at=?
                WHERE {id_column}=? AND run_id=? AND group_id=?
                  AND state='injected'""",
                (now, adopted_via, _json(evidence), now, item_id, run_id, group_id),
            )
            changed += cursor.rowcount
        await db.commit()
    return changed


async def mark_executed(
    *,
    kind: UsageKind,
    item_ids: Sequence[str],
    run_id: str,
    group_id: int | None,
    evidence: Mapping[str, Any],
) -> int:
    if group_id is None or not item_ids:
        return 0
    require_execution_evidence(evidence)
    table, id_column = _USAGE_STORAGE[kind]
    from ai.memory import _memory_db

    now = int(time.time() * 1000)
    changed = 0
    async with await _memory_db(table, group_id, write=True) as db:
        for item_id in item_ids:
            cursor = await db.execute(
                f"""UPDATE {table}
                SET state='executed',executed_at=?,execution_evidence_json=?,
                    updated_at=?
                WHERE {id_column}=? AND run_id=? AND group_id=?
                  AND state='adopted'""",
                (now, _json(evidence), now, item_id, run_id, group_id),
            )
            changed += cursor.rowcount
        await db.commit()
    return changed


async def mark_verified(
    *,
    kind: UsageKind,
    item_ids: Sequence[str],
    run_id: str,
    group_id: int | None,
    status: UsageState,
    evidence: Mapping[str, Any],
) -> int:
    if group_id is None or not item_ids:
        return 0
    require_verification_evidence(status, evidence)
    table, id_column = _USAGE_STORAGE[kind]
    from ai.memory import _memory_db

    now = int(time.time() * 1000)
    changed_ids: list[str] = []
    async with await _memory_db(table, group_id, write=True) as db:
        for item_id in item_ids:
            cursor = await db.execute(
                f"""UPDATE {table}
                SET state=?,verification_status=?,verified_at=?,
                    verification_evidence_json=?,updated_at=?
                WHERE {id_column}=? AND run_id=? AND group_id=?
                  AND state='executed'""",
                (
                    status.value,
                    status.value,
                    now,
                    _json(evidence),
                    now,
                    item_id,
                    run_id,
                    group_id,
                ),
            )
            if cursor.rowcount == 1:
                changed_ids.append(item_id)

        for item_id in changed_ids:
            if kind is UsageKind.EXPERIENCE:
                if status is UsageState.VERIFIED_SUCCESS:
                    await db.execute(
                        """UPDATE memory_records
                        SET supporting_count=supporting_count+1,
                            confidence=MIN(0.98,confidence+0.03),
                            last_used_at=?,updated_at=?
                        WHERE record_id=? AND group_id=?""",
                        (now, now, item_id, group_id),
                    )
                else:
                    await db.execute(
                        """UPDATE memory_records
                        SET contradicting_count=contradicting_count+1,
                            confidence=MAX(0.05,confidence-0.2),
                            last_used_at=?,updated_at=?,
                            status=CASE WHEN contradicting_count+1>=2
                                THEN 'suspended' ELSE status END
                        WHERE record_id=? AND group_id=?""",
                        (now, now, item_id, group_id),
                    )
            elif status is UsageState.VERIFIED_SUCCESS:
                await db.execute(
                    """UPDATE skills SET success_count=success_count+1,
                    maturity=CASE WHEN maturity='trial' THEN 'active'
                        WHEN maturity='active' AND success_count+1>=3
                        THEN 'stable' ELSE maturity END,
                    updated_at=? WHERE skill_id=? AND group_id=?""",
                    (now, item_id, group_id),
                )
            else:
                await db.execute(
                    """UPDATE skills SET failure_count=failure_count+1,
                    status=CASE WHEN failure_count+1>=2
                        THEN 'suspended' ELSE status END,
                    updated_at=? WHERE skill_id=? AND group_id=?""",
                    (now, item_id, group_id),
                )
        await db.commit()
    return len(changed_ids)


async def record_legacy_completion(
    *,
    kind: UsageKind,
    item_ids: Sequence[str],
    run_id: str,
    group_id: int | None,
    outcome: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    tool_attempts: int = 0,
) -> int:
    """Record shadow telemetry without claiming adoption, execution, or success."""

    if group_id is None or not item_ids:
        return 0
    table, id_column = _USAGE_STORAGE[kind]
    from ai.memory import _memory_db

    now = int(time.time() * 1000)
    changed = 0
    async with await _memory_db(table, group_id, write=True) as db:
        for item_id in item_ids:
            if kind is UsageKind.EXPERIENCE:
                cursor = await db.execute(
                    f"""UPDATE {table}
                    SET outcome=?,input_tokens=?,output_tokens=?,tool_attempts=?,
                        updated_at=?
                    WHERE {id_column}=? AND run_id=? AND group_id=?""",
                    (
                        outcome,
                        input_tokens,
                        output_tokens,
                        tool_attempts,
                        now,
                        item_id,
                        run_id,
                        group_id,
                    ),
                )
            else:
                cursor = await db.execute(
                    f"""UPDATE {table} SET outcome=?,updated_at=?
                    WHERE {id_column}=? AND run_id=? AND group_id=?""",
                    (outcome, now, item_id, run_id, group_id),
                )
            changed += cursor.rowcount
        await db.commit()
    return changed
