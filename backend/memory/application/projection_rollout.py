"""Per-group rollout gate for retiring legacy direct Chroma writes."""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass

from memory.ports import MemoryDatabasePort

from .projection_audit import ProjectionAuditResult

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectionRolloutState:
    group_id: int
    consecutive_passes: int
    required_passes: int
    direct_write_enabled: bool
    last_audit_passed: bool
    last_audited_at: int
    last_failure_reason: str

    def as_dict(self) -> dict[str, int | bool | str]:
        return asdict(self)


class BotMemoryProjectionRolloutGate:
    """Fail-open gate driven by consecutive complete shadow-audit passes."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        *,
        required_passes: int = 3,
    ) -> None:
        if required_passes < 1:
            raise ValueError("projection rollout required_passes must be positive")
        self._database = database
        self._required_passes = required_passes

    async def record_audit(
        self, result: ProjectionAuditResult
    ) -> ProjectionRolloutState:
        passed, failure_reason = _qualifies_for_rollout(result)
        return await self._record(
            result.group_id,
            passed=passed,
            failure_reason=failure_reason,
        )

    async def record_failure(
        self, group_id: int, reason: str = "audit_error"
    ) -> ProjectionRolloutState:
        return await self._record(
            group_id,
            passed=False,
            failure_reason=reason,
        )

    async def direct_write_enabled(self, group_id: int) -> bool:
        """Return True on missing state or storage failure to preserve writes."""
        if group_id <= 0:
            return True
        try:
            async with await self._database.connect(
                "memory_projection_rollout", group_id, write=False
            ) as connection:
                async with connection.execute(
                    """SELECT direct_write_enabled
                    FROM memory_projection_rollout WHERE group_id=?""",
                    (group_id,),
                ) as cursor:
                    row = await cursor.fetchone()
            return row is None or bool(row[0])
        except Exception:
            log.exception(
                "memory rollout gate lookup failed open for group %d", group_id
            )
            return True

    async def _record(
        self,
        group_id: int,
        *,
        passed: bool,
        failure_reason: str,
    ) -> ProjectionRolloutState:
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        now = int(time.time() * 1000)
        async with await self._database.connect(
            "memory_projection_rollout", group_id, write=True
        ) as connection:
            if passed:
                await connection.execute(
                    """INSERT INTO memory_projection_rollout
                    (group_id,consecutive_passes,required_passes,
                     direct_write_enabled,last_audit_passed,last_audited_at,
                     last_failure_reason,updated_at)
                    VALUES (?,1,?,CASE WHEN ? <= 1 THEN 0 ELSE 1 END,1,?,'',?)
                    ON CONFLICT(group_id) DO UPDATE SET
                      consecutive_passes=MIN(
                        memory_projection_rollout.consecutive_passes + 1,
                        excluded.required_passes
                      ),
                      required_passes=excluded.required_passes,
                      direct_write_enabled=CASE
                        WHEN memory_projection_rollout.consecutive_passes + 1
                             >= excluded.required_passes THEN 0
                        ELSE 1
                      END,
                      last_audit_passed=1,
                      last_audited_at=excluded.last_audited_at,
                      last_failure_reason='',
                      updated_at=excluded.updated_at""",
                    (
                        group_id,
                        self._required_passes,
                        self._required_passes,
                        now,
                        now,
                    ),
                )
            else:
                await connection.execute(
                    """INSERT INTO memory_projection_rollout
                    (group_id,consecutive_passes,required_passes,
                     direct_write_enabled,last_audit_passed,last_audited_at,
                     last_failure_reason,updated_at)
                    VALUES (?,0,?,1,0,?,?,?)
                    ON CONFLICT(group_id) DO UPDATE SET
                      consecutive_passes=0,
                      required_passes=excluded.required_passes,
                      direct_write_enabled=1,
                      last_audit_passed=0,
                      last_audited_at=excluded.last_audited_at,
                      last_failure_reason=excluded.last_failure_reason,
                      updated_at=excluded.updated_at""",
                    (
                        group_id,
                        self._required_passes,
                        now,
                        failure_reason[:200],
                        now,
                    ),
                )
            await connection.commit()
            async with connection.execute(
                """SELECT group_id,consecutive_passes,required_passes,
                    direct_write_enabled,last_audit_passed,last_audited_at,
                    last_failure_reason
                FROM memory_projection_rollout WHERE group_id=?""",
                (group_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return ProjectionRolloutState(
            group_id=int(row[0]),
            consecutive_passes=int(row[1]),
            required_passes=int(row[2]),
            direct_write_enabled=bool(row[3]),
            last_audit_passed=bool(row[4]),
            last_audited_at=int(row[5]),
            last_failure_reason=str(row[6] or ""),
        )


def _qualifies_for_rollout(
    result: ProjectionAuditResult,
) -> tuple[bool, str]:
    if result.truncated:
        return False, "truncated"
    if result.snapshot_changed:
        return False, "snapshot_changed"
    if result.canonical_total <= 0:
        return False, "no_canonical_records"
    if result.canonical_sampled != result.canonical_total:
        return False, "incomplete_canonical_sample"
    if result.outbox_pending:
        return False, "outbox_pending"
    failures = (
        ("missing", result.missing),
        ("content_mismatched", result.content_mismatched),
        ("metadata_mismatched", result.metadata_mismatched),
        ("orphaned", result.orphaned),
        ("invalid_canonical", result.invalid_canonical),
    )
    for name, count in failures:
        if count:
            return False, name
    if result.matched != result.canonical_total:
        return False, "match_count"
    return True, ""
