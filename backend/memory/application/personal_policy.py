"""Canonical explicit ABAC policy for Personal Vault authorization."""
from __future__ import annotations

import time

from memory.infrastructure import PersonalVaultDatabase
from memory.ports import PersonalVaultPolicyPort


class SQLitePersonalVaultPolicy(PersonalVaultPolicyPort):
    def __init__(self, database: PersonalVaultDatabase | None = None) -> None:
        self._database = database or PersonalVaultDatabase()

    async def evaluate_rule(
        self, *, user_id: int, subject_type: str, subject_id: str,
        object_type: str, object_id: str, action: str,
    ) -> bool | None:
        async with self._database.connect(user_id) as db:
            async with db.execute(
                """SELECT subject_type,subject_id,object_type,object_id,action,effect
                   FROM personal_access_control_actions
                   WHERE user_id=? AND subject_type IN (?, '*')
                     AND subject_id IN (?, '*') AND object_type IN (?, '*')
                     AND object_id IN (?, '*') AND action IN (?, '*')""",
                (user_id, subject_type.strip(), subject_id.strip(), object_type.strip(),
                 object_id.strip(), action.strip()),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return None
        specificity = lambda row: sum(value != "*" for value in row[:5])
        highest = max(specificity(row) for row in rows)
        return not any(str(row[5]) == "deny" for row in rows if specificity(row) == highest)

    async def record_audit(
        self, *, user_id: int, actor_id: str, scope_kind: str,
        group_id: int | None, bot_id: int | None, action: str,
        allowed: bool, reason: str,
    ) -> None:
        async with self._database.connect(user_id) as db:
            await db.execute(
                """INSERT INTO personal_acl_audit_events
                   (user_id,actor_id,scope_kind,group_id,bot_id,action,allowed,reason,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, actor_id, scope_kind, group_id, bot_id, action,
                 int(allowed), reason[:1000], int(time.time() * 1000)),
            )
            await db.commit()
