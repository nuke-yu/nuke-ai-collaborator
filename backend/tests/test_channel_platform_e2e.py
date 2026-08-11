import json
import os
import tempfile
import time
import unittest

import aiosqlite

from channels.bridge import (
    BindingStatus,
    ChannelBinding,
    ChannelBindingStore,
    GroupChannelOutboxRelay,
    GroupChannelOutboxWriter,
    GroupRelayResult,
    IntegrationMember,
    IntegrationMemberStore,
)
from channels.bridge.outbound import OutboundEventProjector
from channels.connectors import (
    ConnectorHttpClient,
    ConnectorHttpResponse,
    FeishuConnector,
    WechatIlinkConnector,
)
from channels.inbound_runtime import ChannelInboundService
from channels.platform_runtime import ChannelPlatformService
from channels.runtime import ChannelDeliveryDispatcher
from channels.stores import ChannelStore, DeliveryState


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": json_body})
        return self.responses.pop(0)


class TestChannelPlatformE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="channel-platform-e2e-")
        self.channel_path = os.path.join(self.temp.name, "channel.db")
        self.group_path = os.path.join(self.temp.name, "group.db")
        self.store = ChannelStore(self.channel_path)
        self.bindings = ChannelBindingStore(self.channel_path)
        self.members = IntegrationMemberStore(self.channel_path)
        await self.store.initialize()
        await self.bindings.initialize()
        await self.members.initialize()
        self.dispatched = []
        self.inbound = ChannelInboundService(
            self.store, self.bindings, self.members, self._dispatch
        )
        self.platforms = ChannelPlatformService(self.inbound)

    async def asyncTearDown(self):
        await self.platforms.stop()
        self.temp.cleanup()

    async def _dispatch(self, route, member):
        self.dispatched.append((route, member))

    async def _activate(self, binding):
        await self.bindings.create(binding)
        await self.bindings.transition(binding.binding_id, BindingStatus.PENDING_APPROVAL)
        await self.bindings.transition(binding.binding_id, BindingStatus.ACTIVE)
        await self.members.create(IntegrationMember(
            integration_member_id=900 + len(self.dispatched),
            binding_id=binding.binding_id,
            group_id=binding.group_id,
            channel_instance_id=binding.channel_instance_id,
            display_name=binding.channel_instance_id,
        ))

    async def _outbox_to_delivery(self, envelope):
        async with aiosqlite.connect(self.group_path) as db:
            await db.execute("BEGIN")
            self.assertTrue(await GroupChannelOutboxWriter.append(db, envelope))
            await db.commit()
        relay = GroupChannelOutboxRelay(self.group_path, self.store)
        self.assertEqual(await relay.relay_once(), GroupRelayResult.FORWARDED)

    async def test_feishu_webhook_to_group_and_group_outbox_to_openapi(self):
        binding = ChannelBinding(
            binding_id="binding-feishu", channel_instance_id="feishu:prod",
            external_tenant_id="tenant-feishu", external_conversation_id="chat-feishu",
            group_id=7, default_bot_id=42,
        )
        await self._activate(binding)
        transport = _Transport([
            ConnectorHttpResponse(200, {
                "code": 0, "tenant_access_token": "tenant-token", "expire": 7200,
            }, {}),
            ConnectorHttpResponse(200, {
                "code": 0, "data": {"message_id": "om-delivered"},
            }, {}),
        ])
        connector = FeishuConnector(
            channel_instance_id="feishu:prod", app_id="cli_app",
            app_secret="app-secret", verification_token="verify-token",
            http=ConnectorHttpClient("feishu", transport, retry_delay=0),
        )
        self.platforms.register_feishu("feishu:prod", connector)
        event = {
            "schema": "2.0",
            "header": {
                "event_id": "evt-feishu", "event_type": "im.message.receive_v1",
                "create_time": str(int(time.time() * 1000)), "token": "verify-token",
                "app_id": "cli_app", "tenant_key": "tenant-feishu",
            },
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou-user"}},
                "message": {
                    "message_id": "om-inbound", "chat_id": "chat-feishu",
                    "chat_type": "group", "message_type": "text",
                    "content": json.dumps({"text": "请检查构建"}, ensure_ascii=False),
                },
            },
        }
        raw = json.dumps(event, ensure_ascii=False).encode()
        await self.platforms.ingest_feishu(
            "feishu:prod", event, raw_body=raw, headers={}
        )
        await self.platforms.ingest_feishu(
            "feishu:prod", event, raw_body=raw, headers={}
        )
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(self.dispatched[0][0].target_bot_id, 42)

        outbound = OutboundEventProjector(
            (await self.bindings.get(binding.binding_id))
        ).project(
            "workflow.completed", {"summary": "构建完成"}, event_id="workflow-feishu-1"
        )
        await self._outbox_to_delivery(outbound)
        dispatcher = ChannelDeliveryDispatcher(
            self.store, connector, channel_instance_id="feishu:prod"
        )
        self.assertTrue(await dispatcher.run_once())
        self.assertEqual(
            (await self.store.get_delivery(outbound.idempotency_key))["state"],
            DeliveryState.SENT,
        )
        self.assertEqual(transport.calls[-1]["body"]["receive_id"], "chat-feishu")

    async def test_personal_wechat_poll_to_group_and_context_reply(self):
        binding = ChannelBinding(
            binding_id="binding-wechat", channel_instance_id="wechat:personal",
            external_tenant_id="wx-bot", external_conversation_id="wx-user",
            group_id=8, default_bot_id=51,
        )
        await self._activate(binding)
        transport = _Transport([
            ConnectorHttpResponse(200, {
                "ret": 0, "get_updates_buf": "cursor-1",
                "msgs": [{
                    "message_id": "wx-inbound", "from_user_id": "wx-user",
                    "context_token": "reply-context-secret",
                    "item_list": [{"type": 1, "text_item": {"text": "进度怎么样"}}],
                }],
            }, {}),
            ConnectorHttpResponse(200, {"ret": 0}, {}),
        ])
        connector = WechatIlinkConnector(
            channel_instance_id="wechat:personal", bot_id="wx-bot",
            bot_token="wx-bot-secret", store=self.store,
            on_inbound=self.platforms.ingest_wechat,
            http=ConnectorHttpClient("wechat", transport, retry_delay=0),
        )
        self.platforms.register_wechat("wechat:personal", connector)
        poll = await connector.poll_once()
        self.assertEqual((poll.dispatched, poll.ignored), (1, 0))
        self.assertEqual(self.dispatched[0][0].target_bot_id, 51)

        outbound = OutboundEventProjector(
            (await self.bindings.get(binding.binding_id))
        ).project(
            "task_stuck", {"message": "等待人工确认"}, event_id="workflow-wechat-1"
        )
        await self._outbox_to_delivery(outbound)
        dispatcher = ChannelDeliveryDispatcher(
            self.store, connector, channel_instance_id="wechat:personal"
        )
        self.assertTrue(await dispatcher.run_once())
        send = transport.calls[-1]["body"]["msg"]
        self.assertEqual(send["to_user_id"], "wx-user")
        self.assertEqual(send["context_token"], "reply-context-secret")
        persisted = await self.store.get_connector_state(
            "wechat:personal", "reply_contexts"
        )
        self.assertNotIn("reply-context-secret", json.dumps(persisted))


if __name__ == "__main__":
    unittest.main()
