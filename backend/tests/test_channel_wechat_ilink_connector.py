import json
import os
import tempfile
import time
import unittest

from channels.connectors import (
    ConnectorHttpClient,
    ConnectorHttpResponse,
    WechatIlinkConnector,
    WechatIlinkError,
    WechatIlinkLoginClient,
    WechatIlinkSessionExpired,
)
from channels.core import ChannelConversation, ChannelIdentity, OutboundEnvelope
from channels.stores import ChannelStore


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": json_body})
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class TestWechatIlinkConnector(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wechat-ilink-")
        self.store = ChannelStore(os.path.join(self.temp.name, "channel.db"))
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_poll_persists_cursor_and_encrypted_reply_context(self):
        transport = _Transport([ConnectorHttpResponse(200, {
            "ret": 0,
            "get_updates_buf": "cursor-2",
            "msgs": [{
                "message_id": "wx-message-1",
                "from_user_id": "wx-user-1",
                "context_token": "Authorization: Bearer real-context-secret",
                "item_list": [{"type": 1, "text_item": {"text": "请检查构建"}}],
            }],
        }, {})])
        received = []
        connector = self._connector(transport, received)
        result = await connector.poll_once(now=1_700_000_000)
        self.assertEqual((result.received, result.dispatched), (1, 1))
        self.assertEqual(received[0][1].text, "请检查构建")
        self.assertNotIn("context_token", received[0][1].raw)
        self.assertEqual(
            await self.store.get_connector_state("wechat:personal", "sync_cursor"),
            {"get_updates_buf": "cursor-2"},
        )
        contexts = await self.store.get_connector_state("wechat:personal", "reply_contexts")
        self.assertNotIn("real-context-secret", json.dumps(contexts))
        self.assertTrue(connector.can_reply("wx-user-1", now=1_700_000_001))

    async def test_encrypted_context_survives_restart_and_sends_with_stable_client_id(self):
        poll_transport = _Transport([ConnectorHttpResponse(200, {
            "ret": 0, "get_updates_buf": "cursor",
            "msgs": [{
                "from_user_id": "wx-user-1", "context_token": "context-1",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }],
        }, {})])
        await self._connector(poll_transport, []).poll_once(now=int(time.time()))

        send_transport = _Transport([ConnectorHttpResponse(200, {"ret": 0}, {})])
        restarted = self._connector(send_transport, [])
        receipt = await restarted.send(_outbound("event-1"))
        request = send_transport.calls[0]
        self.assertEqual(request["body"]["msg"]["context_token"], "context-1")
        self.assertEqual(request["body"]["msg"]["client_id"], receipt.external_message_id)
        self.assertEqual(request["headers"]["Authorization"], "Bearer bot-secret-token")

    async def test_long_text_chunks_use_deterministic_ids(self):
        await self._seed_context()
        transport = _Transport([
            ConnectorHttpResponse(200, {"ret": 0}, {}),
            ConnectorHttpResponse(200, {"ret": 0}, {}),
        ])
        connector = self._connector(transport, [])
        envelope = _outbound("long-event", text="x" * 4_001)
        receipt = await connector.send(envelope)
        self.assertTrue(receipt.external_message_id.startswith("ilink-batch:"))
        self.assertEqual([len(call["body"]["msg"]["item_list"][0]["text_item"]["text"]) for call in transport.calls], [4000, 1])
        self.assertNotEqual(
            transport.calls[0]["body"]["msg"]["client_id"],
            transport.calls[1]["body"]["msg"]["client_id"],
        )

    async def test_partial_long_message_failure_is_ambiguous(self):
        from channels.connectors import WechatIlinkAmbiguousDelivery

        await self._seed_context()
        transport = _Transport([
            ConnectorHttpResponse(200, {"ret": 0}, {}),
            ConnectorHttpResponse(500, {}, {}),
        ])
        connector = self._connector(transport, [])
        with self.assertRaises(WechatIlinkAmbiguousDelivery) as caught:
            await connector.send(_outbound("partial-event", text="x" * 4_001))
        self.assertTrue(caught.exception.ambiguous)
        self.assertEqual(caught.exception.completed_chunks, (0,))
        self.assertEqual(caught.exception.total_chunks, 2)
        self.assertEqual(len(transport.calls), 2)

    async def test_expired_context_and_session_expiry_fail_closed(self):
        await self._seed_context(now=100)
        connector = self._connector(_Transport([]), [])
        await connector._ensure_state_loaded(now=100 + 86_401)
        with self.assertRaisesRegex(WechatIlinkError, "recent inbound"):
            await connector.send(_outbound("expired"))

        expired = self._connector(_Transport([
            ConnectorHttpResponse(200, {"ret": -14, "errmsg": "session expired"}, {})
        ]), [])
        with self.assertRaises(WechatIlinkSessionExpired):
            await expired.poll_once()

    async def test_attachment_media_reference_is_encrypted(self):
        transport = _Transport([ConnectorHttpResponse(200, {
            "ret": 0, "get_updates_buf": "cursor",
            "msgs": [{
                "from_user_id": "wx-user-1", "context_token": "ctx",
                "item_list": [{
                    "type": 2,
                    "image_item": {"media": {
                        "encrypt_query_param": "download-secret-param",
                        "aes_key": "media-secret-key",
                    }},
                }],
            }],
        }, {})])
        received = []
        await self._connector(transport, received).poll_once(now=1_700_000_000)
        reference = received[0][1].attachments[0]["platform_ref"]
        self.assertNotIn("download-secret-param", reference)
        self.assertNotIn("media-secret-key", reference)
        self.assertEqual(
            self._connector(_Transport([]), []).decrypt_media_reference(reference),
            {"encrypt_query_param": "download-secret-param", "aes_key": "media-secret-key"},
        )

    async def test_dispatch_failure_does_not_advance_cursor(self):
        transport = _Transport([ConnectorHttpResponse(200, {
            "ret": 0, "get_updates_buf": "must-retry",
            "msgs": [{
                "from_user_id": "wx-user-1", "context_token": "ctx",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }],
        }, {})])

        async def failing_inbound(instance_id, envelope):
            raise RuntimeError("supervisor unavailable")

        connector = WechatIlinkConnector(
            channel_instance_id="wechat:personal", bot_id="wx-bot-1",
            bot_token="bot-secret-token", store=self.store,
            on_inbound=failing_inbound,
            http=ConnectorHttpClient("wechat", transport, retry_delay=0),
        )
        with self.assertRaisesRegex(RuntimeError, "supervisor unavailable"):
            await connector.poll_once(now=int(time.time()))
        self.assertIsNone(
            await self.store.get_connector_state("wechat:personal", "sync_cursor")
        )

    async def test_malformed_message_is_counted_without_poisoning_cursor(self):
        transport = _Transport([ConnectorHttpResponse(200, {
            "ret": 0, "get_updates_buf": "after-poison",
            "msgs": [{"from_user_id": "wx-user-1", "item_list": "invalid"}],
        }, {})])
        result = await self._connector(transport, []).poll_once(now=int(time.time()))
        self.assertEqual((result.received, result.dispatched, result.ignored), (1, 0, 1))
        self.assertEqual(
            await self.store.get_connector_state("wechat:personal", "sync_cursor"),
            {"get_updates_buf": "after-poison"},
        )

    async def test_unauthorized_conversation_does_not_block_account_cursor(self):
        transport = _Transport([ConnectorHttpResponse(200, {
            "ret": 0, "get_updates_buf": "after-unauthorized",
            "msgs": [{
                "from_user_id": "unbound-user", "context_token": "ctx",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }],
        }, {})])

        async def reject_inbound(instance_id, envelope):
            return False

        connector = WechatIlinkConnector(
            channel_instance_id="wechat:personal", bot_id="wx-bot-1",
            bot_token="bot-secret-token", store=self.store,
            on_inbound=reject_inbound,
            http=ConnectorHttpClient("wechat", transport, retry_delay=0),
        )
        result = await connector.poll_once(now=int(time.time()))
        self.assertEqual((result.dispatched, result.ignored), (0, 1))
        self.assertEqual(
            await self.store.get_connector_state("wechat:personal", "sync_cursor"),
            {"get_updates_buf": "after-unauthorized"},
        )

    async def _seed_context(self, now=None):
        now = int(time.time()) if now is None else now
        transport = _Transport([ConnectorHttpResponse(200, {
            "ret": 0, "get_updates_buf": "cursor",
            "msgs": [{
                "from_user_id": "wx-user-1", "context_token": "context-1",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }],
        }, {})])
        await self._connector(transport, []).poll_once(now=now)

    def _connector(self, transport, received):
        async def inbound(instance_id, envelope):
            received.append((instance_id, envelope))

        return WechatIlinkConnector(
            channel_instance_id="Wechat:Personal",
            bot_id="wx-bot-1",
            bot_token="bot-secret-token",
            store=self.store,
            on_inbound=inbound,
            http=ConnectorHttpClient("wechat", transport, retry_delay=0),
        )


class TestWechatIlinkLogin(unittest.IsolatedAsyncioTestCase):
    async def test_qrcode_and_confirmed_credentials(self):
        transport = _Transport([
            ConnectorHttpResponse(200, {
                "ret": 0, "qrcode": "qr-id", "qrcode_img_content": "https://qr-content"
            }, {}),
            ConnectorHttpResponse(200, {
                "ret": 0, "status": "confirmed", "bot_token": "secret-token",
                "ilink_bot_id": "bot-id", "ilink_user_id": "owner-id",
                "baseurl": "https://ilinkai.weixin.qq.com",
            }, {}),
        ])
        login = WechatIlinkLoginClient(ConnectorHttpClient("wechat-login", transport, retry_delay=0))
        qr = await login.get_qrcode()
        status = await login.poll_qrcode(qr["qrcode_id"])
        self.assertEqual(qr["qrcode_content"], "https://qr-content")
        self.assertEqual(status.status, "confirmed")
        self.assertEqual(status.bot_token, "secret-token")
        self.assertIn("qrcode=qr-id", transport.calls[1]["url"])


def _outbound(key, *, text="构建完成"):
    return OutboundEnvelope(
        identity=ChannelIdentity("wechat", "wx-bot-1"),
        conversation=ChannelConversation("wx-user-1", "direct"),
        event_type="workflow.completed",
        payload={"event": {"summary": text}},
        idempotency_key=key,
        source_event_id=f"source-{key}",
        channel_instance_id="wechat:personal",
    )


if __name__ == "__main__":
    unittest.main()
