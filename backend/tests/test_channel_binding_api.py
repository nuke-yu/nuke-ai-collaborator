import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
from fastapi import HTTPException

import db
from api.channels import (
    ChannelBindingApprovalRequest,
    ChannelBindingCreateRequest,
    ChannelBindingTransitionRequest,
    approve_group_channel_binding,
    create_group_channel_binding,
    list_group_channel_bindings,
    submit_group_channel_binding,
    transition_group_channel_binding,
)
from api.deps import require_group_owner
from channels.bridge import ChannelBindingStore, IntegrationMemberStore
from channels import initialize_channel_schema


class _Delivery:
    def snapshot(self):
        return {"registered_channels": ["feishu:prod", "wechat:personal"]}


class TestChannelBindingApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.mkdtemp(prefix="channel-binding-api-")
        self.central = os.path.join(self.temp, "central.db")
        self.channel = os.path.join(self.temp, "channel.db")
        self.original_db = db.DB_PATH
        db.DB_PATH = self.central
        await db.init_central_db(self.central)
        async with db.connect(self.central) as conn:
            await conn.execute("INSERT INTO groups(id,name) VALUES(7,'project')")
            await conn.executemany(
                "INSERT INTO members(id,group_id,name,type) VALUES(?,?,?,'bot')",
                [(42, 7, "研发"), (43, 7, "QA")],
            )
            await conn.executemany(
                "INSERT INTO users(id,username,password_hash) VALUES(?,?,?)",
                [(1, "owner", "hash"), (2, "member", "hash")],
            )
            await conn.executemany(
                "INSERT INTO group_memberships(user_id,group_id,role) VALUES(?,?,?)",
                [(1, 7, "owner"), (2, 7, "member")],
            )
            await conn.commit()
        await initialize_channel_schema(self.channel)
        self.user = {"uid": 1, "sub": "owner"}
        self.request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(channel_delivery=_Delivery())),
            url=SimpleNamespace(path="/api/channels/groups/7/bindings"),
            method="POST",
        )

    async def asyncTearDown(self):
        db.DB_PATH = self.original_db
        shutil.rmtree(self.temp, ignore_errors=True)

    async def test_owner_can_configure_approve_suspend_and_resume_integration(self):
        with patch("api.channels.channel_bridge_db_path", return_value=self.channel):
            created = await create_group_channel_binding(
                7,
                ChannelBindingCreateRequest(
                    channel_instance_id="Feishu:Prod",
                    external_tenant_id="tenant-1",
                    external_conversation_id="chat-1",
                    default_bot_id=42,
                    allowed_bot_ids=[43],
                    mention_required=True,
                    inbound_policy={"bot_mentions": {"研发": 42, "QA": 43}},
                ),
                self.request,
                self.user,
            )
            binding_id = created["binding_id"]
            self.assertEqual(created["status"], "configured")
            submitted = await submit_group_channel_binding(
                7, binding_id, self.request, self.user
            )
            self.assertEqual(submitted["status"], "pending_approval")
            approved = await approve_group_channel_binding(
                7, binding_id,
                ChannelBindingApprovalRequest(
                    display_name="飞书项目群",
                    metadata={"note": "Authorization: Bearer real-binding-secret-token-12345"},
                ),
                self.request, self.user,
            )
            member_id = approved["integration_member"]["id"]
            self.assertEqual(approved["status"], "active")
            self.assertNotIn("real-binding-secret-token-12345", str(approved))
            suspended = await transition_group_channel_binding(
                7, binding_id, ChannelBindingTransitionRequest(target="suspended"),
                self.request, self.user,
            )
            self.assertEqual(suspended["status"], "suspended")
            self.assertEqual(
                str((await IntegrationMemberStore(self.channel).get(member_id)).status),
                "suspended",
            )
            resumed = await transition_group_channel_binding(
                7, binding_id, ChannelBindingTransitionRequest(target="active"),
                self.request, self.user,
            )
            self.assertEqual(resumed["status"], "active")
            self.assertEqual(
                str((await IntegrationMemberStore(self.channel).get(member_id)).status),
                "active",
            )
            listed = await list_group_channel_bindings(7, self.user)
            self.assertEqual(listed["bindings"][0]["integration_member"]["id"], member_id)

    async def test_approval_rolls_back_if_member_insert_conflicts(self):
        with patch("api.channels.channel_bridge_db_path", return_value=self.channel):
            created = await create_group_channel_binding(
                7,
                ChannelBindingCreateRequest(
                    channel_instance_id="feishu:prod",
                    external_tenant_id="tenant-2",
                    external_conversation_id="chat-2",
                    default_bot_id=42,
                ),
                self.request,
                self.user,
            )
            binding_id = created["binding_id"]
            await submit_group_channel_binding(7, binding_id, self.request, self.user)
            async with aiosqlite.connect(self.channel) as conn:
                await conn.execute(
                    """INSERT INTO channel_integration_members
                       (integration_member_id,binding_id,group_id,channel_instance_id,
                        display_name,avatar,capabilities_json,status,metadata_json,
                        created_at,updated_at)
                       VALUES(999,?,7,'feishu:prod','conflict','','[]','active','{}',1,1)""",
                    (binding_id,),
                )
                await conn.commit()
            with self.assertRaises(HTTPException) as caught:
                await approve_group_channel_binding(
                    7, binding_id,
                    ChannelBindingApprovalRequest(display_name="飞书"),
                    self.request, self.user,
                )
            self.assertEqual(caught.exception.status_code, 409)
            binding = await ChannelBindingStore(self.channel).get(binding_id)
            self.assertEqual(str(binding.status), "pending_approval")

    async def test_configuration_requires_group_owner(self):
        with patch("api.deps.ensure_group_db_ready", new=AsyncMock()):
            owner = await require_group_owner(7, {"uid": 1})
            self.assertEqual(owner["uid"], 1)
            with self.assertRaises(HTTPException) as member_denied:
                await require_group_owner(7, {"uid": 2})
            self.assertEqual(member_denied.exception.status_code, 403)
            with self.assertRaises(HTTPException) as outsider_hidden:
                await require_group_owner(7, {"uid": 99})
            self.assertEqual(outsider_hidden.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
