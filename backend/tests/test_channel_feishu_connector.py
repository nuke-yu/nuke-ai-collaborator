import base64
import hashlib
import json
import unittest

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from channels.connectors import (
    ConnectorAuthError,
    ConnectorHttpClient,
    ConnectorHttpResponse,
    FeishuConnector,
    FeishuConnectorError,
)
from channels.core import ChannelConversation, ChannelIdentity, OutboundEnvelope


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": json_body})
        return self.responses.pop(0)


def _event(*, token="verify-token", create_time="1700000000000", message_type="text", content=None):
    return {
        "schema": "2.0",
        "header": {
            "event_id": "event-1",
            "event_type": "im.message.receive_v1",
            "create_time": create_time,
            "token": token,
            "app_id": "cli_app",
            "tenant_key": "tenant-1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
            "message": {
                "message_id": "om_message",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": message_type,
                "content": json.dumps(content or {"text": "@研发 请检查构建"}, ensure_ascii=False),
                "mentions": [{"key": "@_user_1", "name": "研发", "id": {"open_id": "ou_bot"}}],
                "parent_id": "om_parent",
            },
        },
    }


def _connector(transport=None, **kwargs):
    return FeishuConnector(
        channel_instance_id="Feishu:Prod",
        app_id="cli_app",
        app_secret="app-secret",
        verification_token="verify-token",
        http=ConnectorHttpClient("feishu", transport, retry_delay=0) if transport else None,
        **kwargs,
    )


class TestFeishuInbound(unittest.TestCase):
    def test_normalizes_authenticated_message_and_mentions(self):
        payload = _event()
        raw = json.dumps(payload, ensure_ascii=False).encode()
        result = _connector().handle_webhook(payload, raw_body=raw, now=1_700_000_000)
        envelope = result.envelope
        self.assertEqual(envelope.external_tenant_id, "tenant-1")
        self.assertEqual(envelope.external_group_id, "oc_chat")
        self.assertEqual(envelope.external_user_id, "ou_user")
        self.assertEqual(envelope.text, "@研发 请检查构建")
        self.assertIn("研发", envelope.mentions)
        self.assertEqual(envelope.reply_to_external_id, "om_parent")

    def test_rejects_bad_token_and_stale_event(self):
        connector = _connector()
        bad = _event(token="Authorization: Bearer real-secret-value")
        with self.assertRaisesRegex(ConnectorAuthError, "verification token"):
            connector.handle_webhook(bad, raw_body=json.dumps(bad).encode(), now=1_700_000_000)
        stale = _event(create_time="1600000000000")
        with self.assertRaisesRegex(ConnectorAuthError, "outside replay window"):
            connector.handle_webhook(stale, raw_body=json.dumps(stale).encode(), now=1_700_000_000)

    def test_url_challenge_and_self_echo(self):
        challenge = {"type": "url_verification", "token": "verify-token", "challenge": "abc"}
        self.assertEqual(
            _connector().handle_webhook(challenge, raw_body=json.dumps(challenge).encode()).challenge,
            "abc",
        )
        event = _event()
        event["event"]["sender"] = {"sender_type": "bot", "sender_id": {"app_id": "cli_app"}}
        result = _connector().handle_webhook(event, raw_body=json.dumps(event).encode(), now=1_700_000_000)
        self.assertIsNone(result.envelope)

    def test_decrypts_signed_event(self):
        encrypt_key = "encrypt-key"
        event = _event()
        encrypted = _encrypt(json.dumps(event).encode(), encrypt_key)
        payload = {"encrypt": encrypted}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        timestamp, nonce = "1700000000", "nonce-1"
        signature = hashlib.sha256(timestamp.encode() + nonce.encode() + encrypt_key.encode() + raw).hexdigest()
        result = _connector(encrypt_key=encrypt_key).handle_webhook(
            payload,
            raw_body=raw,
            headers={
                "X-Lark-Request-Timestamp": timestamp,
                "X-Lark-Request-Nonce": nonce,
                "X-Lark-Signature": signature,
            },
            now=1_700_000_000,
        )
        self.assertEqual(result.envelope.external_message_id, "om_message")


class TestFeishuOutbound(unittest.IsolatedAsyncioTestCase):
    async def test_caches_token_and_sends_without_transport_retry(self):
        transport = _Transport([
            ConnectorHttpResponse(200, {"code": 0, "tenant_access_token": "tenant-token", "expire": 7200}, {}),
            ConnectorHttpResponse(200, {"code": 0, "data": {"message_id": "om_sent_1"}}, {}),
            ConnectorHttpResponse(200, {"code": 0, "data": {"message_id": "om_sent_2"}}, {}),
        ])
        connector = _connector(transport)
        first = await connector.send(_outbound("event-1"))
        second = await connector.send(_outbound("event-2"))
        self.assertEqual(first.external_message_id, "om_sent_1")
        self.assertEqual(second.external_message_id, "om_sent_2")
        self.assertEqual(sum("tenant_access_token" in call["url"] for call in transport.calls), 1)
        send_call = transport.calls[1]
        self.assertEqual(send_call["headers"]["Authorization"], "Bearer tenant-token")
        self.assertEqual(send_call["body"]["receive_id"], "oc_chat")
        rendered = json.loads(send_call["body"]["content"])
        self.assertIn("构建完成", rendered["zh_cn"]["content"][0][0]["text"])

    async def test_business_error_is_typed_and_redacted(self):
        transport = _Transport([
            ConnectorHttpResponse(200, {
                "code": 999, "msg": "Authorization: Bearer very-real-secret-token"
            }, {}),
        ])
        connector = _connector(transport)
        with self.assertRaises(FeishuConnectorError) as caught:
            await connector.send(_outbound("event-1"))
        self.assertNotIn("very-real-secret-token", str(caught.exception))


def _outbound(key):
    return OutboundEnvelope(
        identity=ChannelIdentity("feishu", "tenant-1"),
        conversation=ChannelConversation("oc_chat"),
        event_type="workflow.completed",
        payload={"event": {"summary": "构建完成"}},
        idempotency_key=key,
        source_event_id=f"source-{key}",
        channel_instance_id="feishu:prod",
    )


def _encrypt(plaintext: bytes, key: str) -> str:
    iv = bytes(range(16))
    digest = hashes.Hash(hashes.SHA256())
    digest.update(key.encode())
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(digest.finalize()), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()


if __name__ == "__main__":
    unittest.main()
