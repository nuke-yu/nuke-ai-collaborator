"""Group-facing virtual member for an active Channel binding."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

import aiosqlite


class IntegrationMemberStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class IntegrationMember:
    integration_member_id: int
    binding_id: str
    group_id: int
    channel_instance_id: str
    display_name: str
    avatar: str = ""
    capabilities: tuple[str, ...] = ("receive", "send")
    status: IntegrationMemberStatus = IntegrationMemberStatus.ACTIVE
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.integration_member_id <= 0 or self.group_id <= 0:
            raise ValueError("integration_member_id and group_id must be positive")
        for field_name in ("binding_id", "channel_instance_id", "display_name"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.status, IntegrationMemberStatus):
            object.__setattr__(self, "status", IntegrationMemberStatus(str(self.status)))
        caps = tuple(dict.fromkeys(str(cap).strip() for cap in self.capabilities if str(cap).strip()))
        if any(cap not in {"receive", "send", "audit"} for cap in caps):
            raise ValueError("unsupported integration member capability")
        object.__setattr__(self, "capabilities", caps)
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")
        json.dumps(self.metadata, ensure_ascii=False)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def member_type(self) -> str:
        return "integration"

    @property
    def can_execute_tools(self) -> bool:
        return False

    def to_member_dict(self) -> dict[str, Any]:
        return {
            "id": self.integration_member_id,
            "type": self.member_type,
            "name": self.display_name,
            "avatar_color": self.avatar,
            "group_id": self.group_id,
            "binding_id": self.binding_id,
            "channel_instance_id": self.channel_instance_id,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "can_execute_tools": self.can_execute_tools,
            "metadata": dict(self.metadata),
        }


class IntegrationMemberStore:
    def __init__(self, path: str | Path):
        self.path = str(path)

    async def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS channel_integration_members (
                   integration_member_id INTEGER PRIMARY KEY,
                   binding_id TEXT NOT NULL UNIQUE,
                   group_id INTEGER NOT NULL,
                   channel_instance_id TEXT NOT NULL,
                   display_name TEXT NOT NULL,
                   avatar TEXT NOT NULL DEFAULT '',
                   capabilities_json TEXT NOT NULL,
                   status TEXT NOT NULL,
                   metadata_json TEXT NOT NULL DEFAULT '{}',
                   created_at INTEGER NOT NULL,
                   updated_at INTEGER NOT NULL
                )"""
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_channel_members_group ON channel_integration_members(group_id, status)")
            await db.commit()

    async def create(self, member: IntegrationMember) -> IntegrationMember:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT status,group_id,channel_instance_id FROM channel_bindings WHERE binding_id=?",
                (member.binding_id,),
            ) as cursor:
                binding = await cursor.fetchone()
            if binding is None:
                raise ValueError("binding not found")
            if binding[0] != "active":
                raise ValueError("integration member requires an active binding")
            if int(binding[1]) != member.group_id or binding[2] != member.channel_instance_id:
                raise ValueError("integration member does not match binding scope")
            now = int(time.time() * 1000)
            try:
                await db.execute(
                    """INSERT INTO channel_integration_members
                       (integration_member_id,binding_id,group_id,channel_instance_id,display_name,
                        avatar,capabilities_json,status,metadata_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        member.integration_member_id, member.binding_id, member.group_id,
                        member.channel_instance_id, member.display_name, member.avatar,
                        json.dumps(member.capabilities), member.status,
                        json.dumps(member.metadata, ensure_ascii=False), now, now,
                    ),
                )
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                raise ValueError("integration member already exists") from exc
        return member

    async def get(self, integration_member_id: int) -> IntegrationMember | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM channel_integration_members WHERE integration_member_id=?",
                (integration_member_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._from_row(dict(row)) if row else None

    async def set_status(self, integration_member_id: int, status: IntegrationMemberStatus) -> bool:
        if not isinstance(status, IntegrationMemberStatus):
            status = IntegrationMemberStatus(str(status))
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE channel_integration_members SET status=?, updated_at=? WHERE integration_member_id=?",
                (status, now, integration_member_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _from_row(row: dict[str, Any]) -> IntegrationMember:
        return IntegrationMember(
            integration_member_id=int(row["integration_member_id"]),
            binding_id=row["binding_id"],
            group_id=int(row["group_id"]),
            channel_instance_id=row["channel_instance_id"],
            display_name=row["display_name"],
            avatar=row["avatar"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            status=IntegrationMemberStatus(row["status"]),
            metadata=json.loads(row["metadata_json"]),
        )
