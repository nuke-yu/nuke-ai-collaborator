"""Atomic approval of a Binding and its Group-facing Integration Member."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import aiosqlite

from .binding import ChannelBinding, ChannelBindingStore
from .member import IntegrationMember, IntegrationMemberStore
from channels.stores import safe_json_for_storage


class ChannelProvisioningConflict(RuntimeError):
    pass


class ChannelIntegrationProvisioner:
    def __init__(self, path: str | Path):
        self.path = str(path)

    async def approve(
        self,
        binding_id: str,
        *,
        display_name: str,
        avatar: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[ChannelBinding, IntegrationMember]:
        display_name = str(display_name or "").strip()
        if not display_name:
            raise ValueError("integration display_name is required")
        metadata_value = dict(metadata or {})
        json.dumps(metadata_value, ensure_ascii=False)
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT * FROM channel_bindings WHERE binding_id=?", (binding_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                raise KeyError(f"unknown channel binding: {binding_id}")
            if row["status"] != "pending_approval":
                await db.rollback()
                raise ChannelProvisioningConflict(
                    "binding must be pending_approval before activation"
                )
            cursor = await db.execute(
                """UPDATE channel_bindings
                   SET status='active',config_version=config_version+1,updated_at=?
                   WHERE binding_id=? AND status='pending_approval'""",
                (now, binding_id),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise ChannelProvisioningConflict("binding changed during approval")
            try:
                member_cursor = await db.execute(
                    """INSERT INTO channel_integration_members
                       (binding_id,group_id,channel_instance_id,display_name,avatar,
                        capabilities_json,status,metadata_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,'active',?,?,?)""",
                    (
                        binding_id, int(row["group_id"]), row["channel_instance_id"],
                        display_name, str(avatar or ""), json.dumps(("receive", "send")),
                        safe_json_for_storage(metadata_value), now, now,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                raise ChannelProvisioningConflict(
                    "integration member already exists for binding"
                ) from exc
            member_id = int(member_cursor.lastrowid)
            await db.commit()
        binding = await ChannelBindingStore(self.path).get(binding_id)
        member = await IntegrationMemberStore(self.path).get(member_id)
        if binding is None or member is None:
            raise RuntimeError("approved Channel integration could not be reloaded")
        return binding, member
