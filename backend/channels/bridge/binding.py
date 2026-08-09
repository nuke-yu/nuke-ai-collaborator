"""Persisted Channel-Group binding state machine.

The store contains only opaque Group/Bot identifiers.  It does not import or
open Group persistence, so a standalone Channel remains independently runnable.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

import aiosqlite

from channels.core import canonical_channel_instance_id
from channels.stores import safe_json_for_storage


class BindingStatus(StrEnum):
    CONFIGURED = "configured"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class BindingConflictError(RuntimeError):
    """Another writer changed the binding after it was read."""


_TRANSITIONS: dict[BindingStatus, frozenset[BindingStatus]] = {
    BindingStatus.CONFIGURED: frozenset({BindingStatus.PENDING_APPROVAL, BindingStatus.REVOKED}),
    BindingStatus.PENDING_APPROVAL: frozenset({BindingStatus.ACTIVE, BindingStatus.REVOKED}),
    BindingStatus.ACTIVE: frozenset({BindingStatus.SUSPENDED, BindingStatus.REVOKED}),
    BindingStatus.SUSPENDED: frozenset({BindingStatus.ACTIVE, BindingStatus.REVOKED}),
    BindingStatus.REVOKED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ChannelBinding:
    binding_id: str
    channel_instance_id: str
    external_tenant_id: str
    external_conversation_id: str
    group_id: int
    default_bot_id: int
    allowed_bot_ids: tuple[int, ...] = ()
    mention_required: bool = False
    inbound_policy: Mapping[str, Any] = field(default_factory=dict)
    outbound_policy: Mapping[str, Any] = field(default_factory=dict)
    status: BindingStatus = BindingStatus.CONFIGURED
    config_version: int = 1
    created_by: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "binding_id", "channel_instance_id", "external_tenant_id",
            "external_conversation_id", "created_by",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if field_name != "created_by" and not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self, "channel_instance_id", canonical_channel_instance_id(self.channel_instance_id)
        )
        if self.group_id <= 0 or self.default_bot_id <= 0:
            raise ValueError("group_id and default_bot_id must be positive")
        allowed = tuple(dict.fromkeys(int(bot_id) for bot_id in self.allowed_bot_ids))
        if any(bot_id <= 0 for bot_id in allowed):
            raise ValueError("allowed_bot_ids must be positive")
        if self.default_bot_id not in allowed:
            allowed = (self.default_bot_id, *allowed)
        object.__setattr__(self, "allowed_bot_ids", allowed)
        if self.config_version <= 0:
            raise ValueError("config_version must be positive")
        if not isinstance(self.status, BindingStatus):
            object.__setattr__(self, "status", BindingStatus(str(self.status)))
        for name in ("inbound_policy", "outbound_policy"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} must be an object")
            json.dumps(value, ensure_ascii=False)
            object.__setattr__(self, name, dict(value))

    def can_transition_to(self, target: BindingStatus) -> bool:
        if not isinstance(target, BindingStatus):
            target = BindingStatus(str(target))
        return target in _TRANSITIONS[self.status]

    def transitioned(self, target: BindingStatus) -> "ChannelBinding":
        if not self.can_transition_to(target):
            raise ValueError(f"invalid binding transition: {self.status} -> {target}")
        return ChannelBinding(**({**self.to_dict(), "status": target}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "channel_instance_id": self.channel_instance_id,
            "external_tenant_id": self.external_tenant_id,
            "external_conversation_id": self.external_conversation_id,
            "group_id": self.group_id,
            "default_bot_id": self.default_bot_id,
            "allowed_bot_ids": list(self.allowed_bot_ids),
            "mention_required": self.mention_required,
            "inbound_policy": dict(self.inbound_policy),
            "outbound_policy": dict(self.outbound_policy),
            "status": self.status,
            "config_version": self.config_version,
            "created_by": self.created_by,
        }


class ChannelBindingStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if not self.path.strip():
            raise ValueError("binding store path is required")

    async def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS channel_bindings (
                   binding_id TEXT PRIMARY KEY,
                   channel_instance_id TEXT NOT NULL,
                   external_tenant_id TEXT NOT NULL,
                   external_conversation_id TEXT NOT NULL,
                   group_id INTEGER NOT NULL,
                   default_bot_id INTEGER NOT NULL,
                   allowed_bot_ids_json TEXT NOT NULL,
                   mention_required INTEGER NOT NULL DEFAULT 0,
                   inbound_policy_json TEXT NOT NULL DEFAULT '{}',
                   outbound_policy_json TEXT NOT NULL DEFAULT '{}',
                   status TEXT NOT NULL,
                   config_version INTEGER NOT NULL,
                   created_by TEXT NOT NULL DEFAULT '',
                   created_at INTEGER NOT NULL,
                   updated_at INTEGER NOT NULL,
                   UNIQUE(channel_instance_id, external_tenant_id, external_conversation_id)
                )"""
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_channel_bindings_group ON channel_bindings(group_id, status)")
            async with db.execute(
                """SELECT binding_id,channel_instance_id,external_tenant_id,external_conversation_id
                   FROM channel_bindings"""
            ) as cursor:
                rows = await cursor.fetchall()
            scopes: dict[tuple[str, str, str], str] = {}
            updates: list[tuple[str, str]] = []
            for binding_id, instance_id, tenant_id, conversation_id in rows:
                canonical = canonical_channel_instance_id(instance_id)
                scope = (canonical, tenant_id, conversation_id)
                if scope in scopes and scopes[scope] != binding_id:
                    raise ValueError("canonical Channel binding migration would merge distinct bindings")
                scopes[scope] = binding_id
                if canonical != instance_id:
                    updates.append((canonical, binding_id))
            if updates:
                await db.executemany(
                    "UPDATE channel_bindings SET channel_instance_id=? WHERE binding_id=?",
                    updates,
                )
            await db.commit()

    async def create(self, binding: ChannelBinding) -> ChannelBinding:
        if binding.status is not BindingStatus.CONFIGURED:
            raise ValueError("new channel binding must enter configured state")
        now = int(time.time() * 1000)
        try:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    """INSERT INTO channel_bindings
                       (binding_id,channel_instance_id,external_tenant_id,external_conversation_id,
                        group_id,default_bot_id,allowed_bot_ids_json,mention_required,
                        inbound_policy_json,outbound_policy_json,status,config_version,created_by,
                        created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        binding.binding_id, binding.channel_instance_id, binding.external_tenant_id,
                        binding.external_conversation_id, binding.group_id, binding.default_bot_id,
                        json.dumps(binding.allowed_bot_ids), int(binding.mention_required),
                        safe_json_for_storage(binding.inbound_policy),
                        safe_json_for_storage(binding.outbound_policy), binding.status,
                        binding.config_version, binding.created_by, now, now,
                    ),
                )
                await db.commit()
        except aiosqlite.IntegrityError as exc:
            raise ValueError("channel binding already exists") from exc
        return binding

    async def get(self, binding_id: str) -> ChannelBinding | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM channel_bindings WHERE binding_id=?", (binding_id,)) as cursor:
                row = await cursor.fetchone()
        return self._from_row(dict(row)) if row else None

    async def list_active_for_group(self, group_id: int) -> list[ChannelBinding]:
        if not Path(self.path).exists():
            return []
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM channel_bindings WHERE group_id=? AND status='active' ORDER BY binding_id",
                (group_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._from_row(dict(row)) for row in rows]

    async def list_for_group(self, group_id: int) -> list[ChannelBinding]:
        if not Path(self.path).exists():
            return []
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM channel_bindings WHERE group_id=? ORDER BY binding_id",
                (group_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._from_row(dict(row)) for row in rows]

    async def list_active(self) -> list[ChannelBinding]:
        if not Path(self.path).exists():
            return []
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM channel_bindings WHERE status='active' ORDER BY binding_id"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._from_row(dict(row)) for row in rows]

    async def resolve_active(
        self,
        channel_instance_id: str,
        external_tenant_id: str,
        external_conversation_id: str,
    ) -> ChannelBinding | None:
        instance_id = canonical_channel_instance_id(channel_instance_id)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM channel_bindings
                   WHERE channel_instance_id=? AND external_tenant_id=?
                     AND external_conversation_id=? AND status='active'""",
                (instance_id, external_tenant_id, external_conversation_id),
            ) as cursor:
                row = await cursor.fetchone()
        return self._from_row(dict(row)) if row else None

    async def transition(self, binding_id: str, target: BindingStatus) -> ChannelBinding:
        current = await self.get(binding_id)
        if current is None:
            raise KeyError(f"unknown channel binding: {binding_id}")
        updated = current.transitioned(target)
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE channel_bindings SET status=?, config_version=?, updated_at=? WHERE binding_id=? AND status=?",
                (updated.status, updated.config_version + 1, now, binding_id, current.status),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise BindingConflictError(f"binding changed concurrently: {binding_id}")
            if target in {BindingStatus.SUSPENDED, BindingStatus.REVOKED}:
                async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='channel_integration_members'") as table_cursor:
                    has_members = await table_cursor.fetchone()
                if has_members:
                    member_status = "suspended" if target is BindingStatus.SUSPENDED else "revoked"
                    await db.execute(
                        "UPDATE channel_integration_members SET status=?, updated_at=? WHERE binding_id=? AND status=?",
                        (member_status, now, binding_id, "active"),
                    )
            elif target is BindingStatus.ACTIVE and current.status is BindingStatus.SUSPENDED:
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='channel_integration_members'"
                ) as table_cursor:
                    has_members = await table_cursor.fetchone()
                if has_members:
                    await db.execute(
                        """UPDATE channel_integration_members
                           SET status='active',updated_at=?
                           WHERE binding_id=? AND status='suspended'""",
                        (now, binding_id),
                    )
            await db.commit()
        return ChannelBinding(**{**updated.to_dict(), "config_version": updated.config_version + 1})

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ChannelBinding:
        return ChannelBinding(
            binding_id=row["binding_id"],
            channel_instance_id=row["channel_instance_id"],
            external_tenant_id=row["external_tenant_id"],
            external_conversation_id=row["external_conversation_id"],
            group_id=int(row["group_id"]),
            default_bot_id=int(row["default_bot_id"]),
            allowed_bot_ids=tuple(json.loads(row["allowed_bot_ids_json"])),
            mention_required=bool(row["mention_required"]),
            inbound_policy=json.loads(row["inbound_policy_json"]),
            outbound_policy=json.loads(row["outbound_policy_json"]),
            status=BindingStatus(row["status"]),
            config_version=int(row["config_version"]),
            created_by=row["created_by"],
        )
