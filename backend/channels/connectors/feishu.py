"""Feishu/Lark webhook ingress and OpenAPI delivery connector.

Message normalization and outbound rendering follow OpenHanako's Feishu
adapter, while lifecycle and Group routing stay owned by Nuke's Supervisor.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from channels.core import (
    ChannelConversation,
    ChannelIdentity,
    DeliveryReceipt,
    InboundEnvelope,
    OutboundEnvelope,
    canonical_channel_instance_id,
)
from executors.redaction import redact_secrets

from .http import ConnectorHttpClient
from .webhook import ConnectorAuthError, ConnectorError


_DOMAINS = {
    "feishu_cn": "https://open.feishu.cn",
    "lark_global": "https://open.larksuite.com",
}


class FeishuConnectorError(ConnectorError):
    """A Feishu protocol or business response is invalid."""


@dataclass(frozen=True, slots=True)
class FeishuWebhookResult:
    challenge: str | None = None
    envelope: InboundEnvelope | None = None


class FeishuConnector:
    """One configured Feishu application/Channel instance."""

    def __init__(
        self,
        *,
        channel_instance_id: str,
        app_id: str,
        app_secret: str,
        verification_token: str,
        encrypt_key: str | None = None,
        region: str = "feishu_cn",
        http: ConnectorHttpClient | None = None,
        replay_window_seconds: int = 300,
    ) -> None:
        self.channel_instance_id = canonical_channel_instance_id(channel_instance_id)
        if self.channel_instance_id.split(":", 1)[0] != "feishu":
            raise ValueError("Feishu channel_instance_id must start with feishu:")
        self.app_id = str(app_id or "").strip()
        self._app_secret = str(app_secret or "").strip()
        self._verification_token = str(verification_token or "").strip()
        self._encrypt_key = str(encrypt_key or "").strip() or None
        if not self.app_id or not self._app_secret or not self._verification_token:
            raise ValueError("Feishu app_id, app_secret and verification_token are required")
        if region not in _DOMAINS:
            raise ValueError(f"unsupported Feishu region: {region}")
        if replay_window_seconds <= 0:
            raise ValueError("replay_window_seconds must be positive")
        self.domain = _DOMAINS[region]
        self.http = http or ConnectorHttpClient("feishu")
        self.replay_window_seconds = replay_window_seconds
        self._tenant_token: str | None = None
        self._tenant_token_expires_at = 0.0

    def handle_webhook(
        self,
        payload: Mapping[str, Any],
        *,
        raw_body: bytes,
        headers: Mapping[str, str] | None = None,
        now: int | None = None,
    ) -> FeishuWebhookResult:
        """Authenticate and normalize one Feishu event callback.

        Durable message replay protection is completed by ``ChannelStore`` at
        the subsequent Supervisor ingress boundary. Here the authenticated
        event timestamp is also constrained to prevent stale callback replay.
        """
        if not isinstance(payload, Mapping) or not isinstance(raw_body, bytes):
            raise FeishuConnectorError("Feishu webhook body must be a JSON object")
        normalized_headers = {
            str(key).lower(): str(value) for key, value in (headers or {}).items()
        }
        body = dict(payload)
        if "encrypt" in body:
            self._verify_encrypted_signature(raw_body, normalized_headers)
            body = self._decrypt_event(str(body.get("encrypt") or ""))
        self._verify_token(body)

        if str(body.get("type") or "") == "url_verification":
            challenge = str(body.get("challenge") or "").strip()
            if not challenge:
                raise FeishuConnectorError("Feishu URL verification has no challenge")
            return FeishuWebhookResult(challenge=challenge)

        header = body.get("header") if isinstance(body.get("header"), Mapping) else {}
        if str(header.get("event_type") or "") != "im.message.receive_v1":
            return FeishuWebhookResult()
        if header.get("app_id") and str(header["app_id"]) != self.app_id:
            raise ConnectorAuthError("Feishu webhook app_id does not match connector")
        self._verify_event_time(header.get("create_time"), now=now)

        event = body.get("event")
        if not isinstance(event, Mapping):
            raise FeishuConnectorError("Feishu message event is missing event data")
        sender = event.get("sender") if isinstance(event.get("sender"), Mapping) else {}
        sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), Mapping) else {}
        if str(sender.get("sender_type") or "") in {"bot", "app"} and str(sender_id.get("app_id") or "") == self.app_id:
            return FeishuWebhookResult()
        message = event.get("message")
        if not isinstance(message, Mapping):
            raise FeishuConnectorError("Feishu message event has no message")
        envelope = self._normalize_message(body, header, sender, sender_id, message)
        return FeishuWebhookResult(envelope=envelope)

    async def send(self, envelope: OutboundEnvelope) -> DeliveryReceipt:
        if envelope.identity.channel != "feishu":
            raise FeishuConnectorError("outbound envelope channel does not match Feishu")
        if envelope.channel_instance_id and envelope.channel_instance_id != self.channel_instance_id:
            raise FeishuConnectorError("outbound envelope instance does not match Feishu connector")
        rendered = _render_outbound(envelope)
        token = await self._tenant_access_token()
        body = {"msg_type": rendered["msg_type"], "content": rendered["content"]}
        if envelope.reply_to_external_id:
            url = f"{self.domain}/open-apis/im/v1/messages/{envelope.reply_to_external_id}/reply"
        else:
            url = f"{self.domain}/open-apis/im/v1/messages?receive_id_type=chat_id"
            body["receive_id"] = envelope.conversation.external_conversation_id
        response = await self.http.request_json(
            "send", "POST", url,
            headers={"Authorization": f"Bearer {token}"},
            json_body=body,
            idempotent=False,
        )
        result = _require_business_success("send", response.body)
        message_id = _first_string(
            _mapping(result.get("data")).get("message_id"),
            result.get("message_id"),
        )
        if not message_id:
            raise FeishuConnectorError("Feishu send response has no message_id")
        return DeliveryReceipt(
            channel="feishu",
            idempotency_key=envelope.idempotency_key,
            status="sent",
            external_message_id=message_id,
        )

    async def close(self) -> None:
        await self.http.close()

    async def _tenant_access_token(self) -> str:
        now = time.monotonic()
        if self._tenant_token and now < self._tenant_token_expires_at:
            return self._tenant_token
        response = await self.http.request_json(
            "tenant-token", "POST",
            f"{self.domain}/open-apis/auth/v3/tenant_access_token/internal",
            json_body={"app_id": self.app_id, "app_secret": self._app_secret},
            idempotent=True,
        )
        body = _require_business_success("tenant-token", response.body)
        token = _first_string(body.get("tenant_access_token"), _mapping(body.get("data")).get("tenant_access_token"))
        if not token:
            raise FeishuConnectorError("Feishu token response has no tenant_access_token")
        try:
            expires_in = max(60, int(body.get("expire") or 7200))
        except (TypeError, ValueError):
            expires_in = 7200
        self._tenant_token = token
        self._tenant_token_expires_at = now + max(1, expires_in - 60)
        return token

    def _verify_token(self, body: Mapping[str, Any]) -> None:
        header = body.get("header") if isinstance(body.get("header"), Mapping) else {}
        supplied = str(header.get("token") or body.get("token") or "")
        if not hmac.compare_digest(supplied, self._verification_token):
            raise ConnectorAuthError("invalid Feishu verification token")

    def _verify_event_time(self, value: Any, *, now: int | None) -> None:
        try:
            timestamp = int(str(value))
        except (TypeError, ValueError) as exc:
            raise ConnectorAuthError("Feishu event create_time is required") from exc
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        current = int(time.time()) if now is None else int(now)
        if abs(current - timestamp) > self.replay_window_seconds:
            raise ConnectorAuthError("Feishu event timestamp is outside replay window")

    def _verify_encrypted_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> None:
        if not self._encrypt_key:
            raise ConnectorAuthError("encrypted Feishu webhook requires encrypt_key")
        timestamp = headers.get("x-lark-request-timestamp", "")
        nonce = headers.get("x-lark-request-nonce", "")
        supplied = headers.get("x-lark-signature", "")
        if not timestamp or not nonce or not supplied:
            raise ConnectorAuthError("encrypted Feishu webhook signature headers are required")
        signed = timestamp.encode() + nonce.encode() + self._encrypt_key.encode() + raw_body
        expected = hashlib.sha256(signed).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ConnectorAuthError("invalid Feishu webhook signature")

    def _decrypt_event(self, encrypted: str) -> dict[str, Any]:
        if not self._encrypt_key or not encrypted:
            raise ConnectorAuthError("invalid encrypted Feishu webhook")
        try:
            from cryptography.hazmat.primitives import hashes, padding
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            ciphertext = base64.b64decode(encrypted, validate=True)
            if len(ciphertext) <= 16:
                raise ValueError("ciphertext is too short")
            digest = hashes.Hash(hashes.SHA256())
            digest.update(self._encrypt_key.encode())
            decryptor = Cipher(
                algorithms.AES(digest.finalize()), modes.CBC(ciphertext[:16])
            ).decryptor()
            padded = decryptor.update(ciphertext[16:]) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
            value = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise ConnectorAuthError("cannot decrypt Feishu webhook") from exc
        if not isinstance(value, dict):
            raise FeishuConnectorError("decrypted Feishu webhook must be an object")
        return value

    def _normalize_message(
        self,
        raw: Mapping[str, Any],
        header: Mapping[str, Any],
        sender: Mapping[str, Any],
        sender_id: Mapping[str, Any],
        message: Mapping[str, Any],
    ) -> InboundEnvelope:
        message_id = str(message.get("message_id") or header.get("event_id") or "").strip()
        chat_id = str(message.get("chat_id") or "").strip()
        user_id = _first_string(sender_id.get("open_id"), sender_id.get("user_id"), sender_id.get("union_id"), sender_id.get("app_id"))
        tenant_id = _first_string(header.get("tenant_key"), header.get("app_id"), self.app_id)
        if not message_id or not chat_id or not user_id or not tenant_id:
            raise FeishuConnectorError("Feishu message identifiers are incomplete")
        text, attachments = _normalize_content(message)
        mentions: list[str] = []
        for mention in message.get("mentions") or ():
            if not isinstance(mention, Mapping):
                continue
            mention_id = mention.get("id") if isinstance(mention.get("id"), Mapping) else {}
            for value in (mention.get("key"), mention.get("name"), mention_id.get("open_id"), mention_id.get("user_id")):
                normalized = str(value or "").strip()
                if normalized and normalized not in mentions:
                    mentions.append(normalized)
        return InboundEnvelope(
            identity=ChannelIdentity("feishu", tenant_id, user_id),
            conversation=ChannelConversation(
                chat_id,
                "group" if str(message.get("chat_type") or "") == "group" else "direct",
            ),
            external_message_id=message_id,
            text=text,
            mentions=tuple(mentions),
            reply_to_external_id=_first_string(message.get("parent_id"), message.get("root_id")) or None,
            attachments=tuple(attachments),
            raw=raw,
        )


def _normalize_content(message: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    raw_content = message.get("content")
    if isinstance(raw_content, Mapping):
        content = dict(raw_content)
    else:
        try:
            content = json.loads(str(raw_content or "{}"))
        except json.JSONDecodeError as exc:
            raise FeishuConnectorError("invalid Feishu message content JSON") from exc
    if not isinstance(content, Mapping):
        raise FeishuConnectorError("Feishu message content must be an object")
    message_type = str(message.get("message_type") or "")
    if message_type == "text":
        return str(content.get("text") or ""), []
    if message_type == "post":
        return _normalize_post(content, str(message.get("message_id") or ""))
    attachment_types = {
        "image": ("image", "image_key"),
        "file": ("file", "file_key"),
        "audio": ("audio", "file_key"),
        "media": ("video", "file_key"),
    }
    if message_type in attachment_types:
        kind, key_name = attachment_types[message_type]
        platform_ref = str(content.get(key_name) or "").strip()
        attachments = [] if not platform_ref else [{
            "type": kind,
            "platform_ref": platform_ref,
            "filename": str(content.get("file_name") or "") or None,
            "message_id": str(message.get("message_id") or ""),
        }]
        return "", attachments
    return f"[Unsupported Feishu message type: {message_type or 'unknown'}]", []


def _normalize_post(content: Mapping[str, Any], message_id: str) -> tuple[str, list[dict[str, Any]]]:
    locale = content.get("zh_cn") or content.get("en_us")
    if not isinstance(locale, Mapping):
        locale = content if isinstance(content.get("content"), list) else {}
    lines: list[str] = []
    attachments: list[dict[str, Any]] = []
    for paragraph in locale.get("content") or ():
        if not isinstance(paragraph, list):
            continue
        line = ""
        for item in paragraph:
            if not isinstance(item, Mapping):
                continue
            tag = str(item.get("tag") or item.get("type") or "")
            if tag in {"text", "md"}:
                line += str(item.get("text") or "")
            elif tag == "a":
                line += str(item.get("text") or item.get("href") or "")
            elif tag == "at":
                line += "@" + str(item.get("user_name") or item.get("name") or item.get("user_id") or "unknown")
            elif tag in {"img", "image", "media"}:
                ref = _first_string(item.get("image_key"), item.get("file_key"))
                if ref:
                    attachments.append({
                        "type": "image" if tag in {"img", "image"} else "video",
                        "platform_ref": ref,
                        "message_id": message_id,
                    })
        if line:
            lines.append(line)
    title = str(locale.get("title") or "").strip()
    return "\n".join(([title] if title else []) + lines), attachments


def _render_outbound(envelope: OutboundEnvelope) -> dict[str, str]:
    event = envelope.payload.get("event")
    payload = event if isinstance(event, Mapping) else envelope.payload
    text = _first_string(*(payload.get(key) for key in ("text", "message", "summary", "error", "title")))
    if not text:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    paragraphs = [[{"tag": "md", "text": line}] for line in text.splitlines() or [""]]
    return {
        "msg_type": "post",
        "content": json.dumps({"zh_cn": {"title": envelope.event_type, "content": paragraphs}}, ensure_ascii=False),
    }


def _require_business_success(operation: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FeishuConnectorError(f"Feishu {operation} response must be an object")
    body = dict(value)
    code = body.get("code")
    if code not in (None, 0, "0"):
        message, _ = redact_secrets(str(body.get("msg") or body.get("message") or "unknown error"))
        raise FeishuConnectorError(f"Feishu {operation} failed: code={code}, message={message}")
    return body


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_string(*values: Any) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""
