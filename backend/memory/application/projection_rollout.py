"""Per-group rollout gate for retiring legacy direct Chroma writes."""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from collections.abc import Callable

from memory.ports import MemoryDatabasePort

from .projection_audit import ProjectionAuditResult

log = logging.getLogger(__name__)

_HARD_FAILURES = frozenset(
    {
        "no_canonical_records",
        "incomplete_canonical_sample",
        "missing",
        "content_mismatched",
        "metadata_mismatched",
        "orphaned",
        "invalid_canonical",
        "match_count",
    }
)


@dataclass(frozen=True, slots=True)
class ProjectionRolloutState:
    group_id: int
    consecutive_passes: int
    required_passes: int
    direct_write_enabled: bool
    last_audit_passed: bool
    last_audited_at: int
    last_failure_reason: str
    qualified_since: int = 0
    cooldown_until: int = 0

    def as_dict(self) -> dict[str, int | bool | str]:
        return asdict(self)


class BotMemoryProjectionRolloutGate:
    """Fail-open gate driven by consecutive complete shadow-audit passes."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        *,
        required_passes: int = 3,
        min_observation_seconds: float = 0,
        min_audit_interval_seconds: float = 0,
        reopen_cooldown_seconds: float = 0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if required_passes < 1:
            raise ValueError("projection rollout required_passes must be positive")
        if min_observation_seconds < 0:
            raise ValueError("min_observation_seconds cannot be negative")
        if min_audit_interval_seconds < 0:
            raise ValueError("min_audit_interval_seconds cannot be negative")
        if reopen_cooldown_seconds < 0:
            raise ValueError("reopen_cooldown_seconds cannot be negative")
        self._database = database
        self._required_passes = required_passes
        self._min_observation_ms = int(min_observation_seconds * 1000)
        self._min_audit_interval_ms = int(min_audit_interval_seconds * 1000)
        self._reopen_cooldown_ms = int(reopen_cooldown_seconds * 1000)
        self._clock = clock

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
        now = int(self._clock() * 1000)
        async with await self._database.connect(
            "memory_projection_rollout", group_id, write=True
        ) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            async with connection.execute(
                """SELECT consecutive_passes,last_audited_at,
                    qualified_since,cooldown_until
                FROM memory_projection_rollout WHERE group_id=?""",
                (group_id,),
            ) as cursor:
                existing = await cursor.fetchone()

            current_passes = int(existing[0]) if existing else 0
            last_audited_at = int(existing[1]) if existing else 0
            qualified_since = int(existing[2]) if existing else 0
            cooldown_until = int(existing[3]) if existing else 0

            if (
                passed
                and last_audited_at
                and now - last_audited_at < self._min_audit_interval_ms
            ):
                await connection.rollback()
                return await self._state(group_id)

            if passed:
                consecutive_passes = min(
                    current_passes + 1, self._required_passes
                )
                if qualified_since == 0:
                    qualified_since = now
                observation_complete = (
                    now - qualified_since >= self._min_observation_ms
                )
                direct_write_enabled = not (
                    consecutive_passes >= self._required_passes
                    and observation_complete
                    and now >= cooldown_until
                )
                last_audit_passed = True
                failure_reason = ""
            else:
                if failure_reason in _HARD_FAILURES:
                    consecutive_passes = 0
                    qualified_since = 0
                else:
                    consecutive_passes = max(0, current_passes - 1)
                    if consecutive_passes == 0:
                        qualified_since = 0
                direct_write_enabled = True
                last_audit_passed = False
                cooldown_until = max(
                    cooldown_until, now + self._reopen_cooldown_ms
                )

            await connection.execute(
                """INSERT INTO memory_projection_rollout
                (group_id,consecutive_passes,required_passes,
                 direct_write_enabled,last_audit_passed,last_audited_at,
                 last_failure_reason,qualified_since,cooldown_until,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(group_id) DO UPDATE SET
                  consecutive_passes=excluded.consecutive_passes,
                  required_passes=excluded.required_passes,
                  direct_write_enabled=excluded.direct_write_enabled,
                  last_audit_passed=excluded.last_audit_passed,
                  last_audited_at=excluded.last_audited_at,
                  last_failure_reason=excluded.last_failure_reason,
                  qualified_since=excluded.qualified_since,
                  cooldown_until=excluded.cooldown_until,
                  updated_at=excluded.updated_at""",
                (
                    group_id,
                    consecutive_passes,
                    self._required_passes,
                    int(direct_write_enabled),
                    int(last_audit_passed),
                    now,
                    failure_reason[:200],
                    qualified_since,
                    cooldown_until,
                    now,
                ),
            )
            await connection.commit()
        return await self._state(group_id)

    async def _state(self, group_id: int) -> ProjectionRolloutState:
        async with await self._database.connect(
            "memory_projection_rollout", group_id, write=False
        ) as connection:
            async with connection.execute(
                """SELECT group_id,consecutive_passes,required_passes,
                    direct_write_enabled,last_audit_passed,last_audited_at,
                    last_failure_reason,qualified_since,cooldown_until
                FROM memory_projection_rollout WHERE group_id=?""",
                (group_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return ProjectionRolloutState(
                group_id=group_id,
                consecutive_passes=0,
                required_passes=self._required_passes,
                direct_write_enabled=True,
                last_audit_passed=False,
                last_audited_at=0,
                last_failure_reason="",
            )
        return ProjectionRolloutState(
            group_id=int(row[0]),
            consecutive_passes=int(row[1]),
            required_passes=int(row[2]),
            direct_write_enabled=bool(row[3]),
            last_audit_passed=bool(row[4]),
            last_audited_at=int(row[5]),
            last_failure_reason=str(row[6] or ""),
            qualified_since=int(row[7]),
            cooldown_until=int(row[8]),
        )


def _qualifies_for_rollout(
    result: ProjectionAuditResult,
) -> tuple[bool, str]:
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
    if result.truncated:
        return False, "truncated"
    return True, ""
