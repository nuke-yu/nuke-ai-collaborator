"""Personal WeChat connector for Tencent's iLink bot protocol.

The wire contract and message behavior are adapted from OpenHanako's
``wechat-adapter.ts`` (itself based on Tencent openclaw-weixin). Unlike that
implementation, reply context tokens are encrypted before durable storage.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote, urlsplit

from cryptography.fernet import Fernet, InvalidToken

from channels.core import (
    ChannelConversation,
    ChannelIdentity,
    DeliveryReceipt,
    InboundEnvelope,
    OutboundEnvelope,
    canonical_channel_instance_id,
)
from channels.stores import ChannelStore
from executors.redaction import redact_secrets

from .http import ConnectorHttpClient
from .webhook import ConnectorError


DEFAULT_WECHAT_ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
_CONTEXT_TTL_SECONDS = 24 * 60 * 60
_MESSAGE_CHUNK_LIMIT = 4_000
_TEXT, _IMAGE, _VOICE, _FILE, _VIDEO = 1, 2, 3, 4, 5


class WechatIlinkError(ConnectorError):
    """A personal WeChat iLink request or payload failed."""


class WechatIlinkSessionExpired(WechatIlinkError):
    """The scanned personal WeChat bot session must be authorized again."""


class WechatIlinkAmbiguousDelivery(WechatIlinkError):
    """A multi-part send partially succeeded and must not auto-retry."""

    ambiguous = True

    def __init__(self, message: str, *, completed_chunks: tuple[int, ...] = (), total_chunks: int = 0):
        super().__init__(message)
        self.completed_chunks = completed_chunks
        self.total_chunks = total_chunks


@dataclass(frozen=True, slots=True)
class WechatPollResult:
    received: int
    dispatched: int
    ignored: int


@dataclass(frozen=True, slots=True)
class WechatLoginStatus:
    status: str
    bot_token: str | None = None
    bot_id: str | None = None
    user_id: str | None = None
    base_url: str | None = None


class WechatIlinkConnector:
    """One scanned personal WeChat bot account and its long-poll state."""

    def __init__(
        self,
        *,
        channel_instance_id: str,
        bot_id: str,
        bot_token: str,
        store: ChannelStore,
        on_inbound: Callable[[str, InboundEnvelope], Awaitable[bool | None]],
        http: ConnectorHttpClient | None = None,
        base_url: str = DEFAULT_WECHAT_ILINK_BASE_URL,
        context_ttl_seconds: int = _CONTEXT_TTL_SECONDS,
    ) -> None:
        self.channel_instance_id = canonical_channel_instance_id(channel_instance_id)
        if self.channel_instance_id.split(":", 1)[0] != "wechat":
            raise ValueError("WeChat channel_instance_id must start with wechat:")
        self.bot_id = str(bot_id or "").strip()
        self._bot_token = str(bot_token or "").strip()
        if not self.bot_id or not self._bot_token:
            raise ValueError("WeChat bot_id and bot_token are required")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("WeChat iLink base_url must be absolute HTTPS")
        if context_ttl_seconds <= 0:
            raise ValueError("context_ttl_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.store = store
        self.on_inbound = on_inbound
        self.http = http or ConnectorHttpClient("wechat")
        self.context_ttl_seconds = context_ttl_seconds
        self._contexts: dict[str, tuple[str, int]] = {}
        self._state_loaded = False
        self._state_lock = asyncio.Lock()
        self._cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(
            b"nuke-wechat-ilink-context-v1\x00" + self._bot_token.encode()
        ).digest()))
        self._token_fingerprint = hashlib.sha256(self._bot_token.encode()).hexdigest()[:16]

    async def poll_once(self, *, now: int | None = None) -> WechatPollResult:
        await self._ensure_state_loaded(now=now)
        cursor_state = await self.store.get_connector_state(
            self.channel_instance_id, "sync_cursor"
        )
        cursor = str(cursor_state.get("get_updates_buf") or "") if isinstance(cursor_state, Mapping) else ""
        response = await self.http.request_json(
            "getupdates", "POST", f"{self.base_url}/ilink/bot/getupdates",
            headers=self._headers(),
            json_body={
                "get_updates_buf": cursor,
                "base_info": {"channel_version": "1.0.0"},
            },
            idempotent=True,
        )
        body = _require_ilink_success("getupdates", response.body)
        messages = body.get("msgs") or []
        if not isinstance(messages, list):
            raise WechatIlinkError("WeChat getupdates msgs must be a list")
        dispatched = 0
        ignored = 0
        current = int(time.time()) if now is None else int(now)
        for raw_message in messages:
            if not isinstance(raw_message, Mapping):
                ignored += 1
                continue
            try:
                envelope = await self._normalize_inbound(raw_message, now=current)
            except WechatIlinkError:
                # A malformed vendor message is poison data, not a transient
                # batch failure. Count it and let the durable cursor advance.
                ignored += 1
                continue
            if envelope is None:
                ignored += 1
                continue
            accepted = await self.on_inbound(self.channel_instance_id, envelope)
            if accepted is False:
                ignored += 1
            else:
                dispatched += 1
        next_cursor = body.get("get_updates_buf")
        if next_cursor is not None:
            await self.store.set_connector_state(
                self.channel_instance_id,
                "sync_cursor",
                {"get_updates_buf": str(next_cursor)},
            )
        return WechatPollResult(len(messages), dispatched, ignored)

    async def start(self) -> None:
        """Load encrypted reply context before polling or delivery starts."""
        await self._ensure_state_loaded()

    async def send(self, envelope: OutboundEnvelope) -> DeliveryReceipt:
        if envelope.identity.channel != "wechat":
            raise WechatIlinkError("outbound envelope channel does not match WeChat")
        if envelope.channel_instance_id and envelope.channel_instance_id != self.channel_instance_id:
            raise WechatIlinkError("outbound envelope instance does not match WeChat connector")
        await self._ensure_state_loaded()
        chat_id = envelope.conversation.external_conversation_id
        context_token = self._get_context(chat_id)
        if not context_token:
            raise WechatIlinkError("WeChat reply requires a recent inbound message from this user")
        text = _render_outbound_text(envelope)
        chunks = [text[index:index + _MESSAGE_CHUNK_LIMIT] for index in range(0, len(text), _MESSAGE_CHUNK_LIMIT)] or [""]
        client_ids: list[str] = []
        for index, chunk in enumerate(chunks):
            client_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{envelope.idempotency_key}:{index}"))
            try:
                response = await self.http.request_json(
                    "sendmessage", "POST", f"{self.base_url}/ilink/bot/sendmessage",
                    headers=self._headers(),
                    json_body={
                        "msg": {
                            "from_user_id": "",
                            "to_user_id": chat_id,
                            "client_id": client_id,
                            "message_type": 2,
                            "message_state": 2,
                            "item_list": [{"type": _TEXT, "text_item": {"text": chunk}}],
                            "context_token": context_token,
                        },
                        "base_info": {"channel_version": "1.0.0"},
                    },
                    idempotent=False,
                )
                _require_ilink_success("sendmessage", response.body)
            except Exception as exc:
                if client_ids:
                    raise WechatIlinkAmbiguousDelivery(
                        "WeChat message batch partially succeeded; operator review is required",
                        completed_chunks=tuple(range(len(client_ids))),
                        total_chunks=len(chunks),
                    ) from exc
                raise
            client_ids.append(client_id)
        external_id = client_ids[0] if len(client_ids) == 1 else "ilink-batch:" + hashlib.sha256("\x00".join(client_ids).encode()).hexdigest()
        return DeliveryReceipt(
            channel="wechat",
            idempotency_key=envelope.idempotency_key,
            status="sent",
            external_message_id=external_id,
        )

    async def close(self) -> None:
        await self.http.close()

    def can_reply(self, external_user_id: str, *, now: int | None = None) -> bool:
        return self._get_context(external_user_id, now=now) is not None

    def decrypt_media_reference(self, platform_ref: str) -> dict[str, str]:
        """Resolve an opaque stored media reference only inside the connector."""
        try:
            value = json.loads(self._cipher.decrypt(str(platform_ref).encode()).decode())
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WechatIlinkError("invalid encrypted WeChat media reference") from exc
        if not isinstance(value, dict) or not value.get("encrypt_query_param"):
            raise WechatIlinkError("invalid encrypted WeChat media reference")
        return {
            "encrypt_query_param": str(value["encrypt_query_param"]),
            "aes_key": str(value.get("aes_key") or ""),
        }

    async def _normalize_inbound(
        self, message: Mapping[str, Any], *, now: int
    ) -> InboundEnvelope | None:
        from_user_id = str(message.get("from_user_id") or "").strip()
        if not from_user_id or from_user_id.endswith("@im.bot"):
            return None
        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self._contexts[from_user_id] = (context_token, now)
            await self._persist_contexts(now=now)
        items = message.get("item_list") or []
        if not isinstance(items, list):
            raise WechatIlinkError("WeChat inbound item_list must be a list")
        text = _extract_text(items)
        attachments: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            attachment = self._attachment(item)
            if attachment:
                attachments.append(attachment)
        if not text and not attachments:
            return None
        message_id = _message_id(message)
        safe_raw = {key: value for key, value in message.items() if key != "context_token"}
        return InboundEnvelope(
            identity=ChannelIdentity("wechat", self.bot_id, from_user_id),
            conversation=ChannelConversation(from_user_id, "direct"),
            external_message_id=message_id,
            text=text,
            attachments=tuple(attachments),
            raw=safe_raw,
        )

    def _attachment(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        item_type = item.get("type")
        specs = {
            _IMAGE: ("image", "image_item", "image/jpeg"),
            _FILE: ("file", "file_item", "application/octet-stream"),
            _VIDEO: ("video", "video_item", "video/mp4"),
        }
        if item_type not in specs:
            return None
        kind, field, mime = specs[item_type]
        value = item.get(field)
        if not isinstance(value, Mapping):
            return None
        media = value.get("media")
        if not isinstance(media, Mapping) or not media.get("encrypt_query_param"):
            return None
        media_ref = {
            "encrypt_query_param": str(media["encrypt_query_param"]),
            "aes_key": str(media.get("aes_key") or value.get("aeskey") or ""),
        }
        encrypted_ref = self._cipher.encrypt(
            json.dumps(media_ref, separators=(",", ":")).encode()
        ).decode()
        attachment = {
            "type": kind,
            "platform_ref": encrypted_ref,
            "mime_type": mime,
        }
        if field == "file_item":
            attachment["filename"] = str(value.get("file_name") or "") or None
            try:
                attachment["size"] = int(value.get("len"))
            except (TypeError, ValueError):
                pass
        return attachment

    async def _ensure_state_loaded(self, *, now: int | None = None) -> None:
        if self._state_loaded:
            return
        async with self._state_lock:
            if self._state_loaded:
                return
            value = await self.store.get_connector_state(
                self.channel_instance_id, "reply_contexts"
            )
            current = int(time.time()) if now is None else int(now)
            if isinstance(value, Mapping) and value.get("token_fingerprint") == self._token_fingerprint:
                encrypted = str(value.get("ciphertext") or "")
                if encrypted:
                    try:
                        decoded = json.loads(self._cipher.decrypt(encrypted.encode()).decode())
                    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise WechatIlinkError("stored WeChat reply context is corrupted") from exc
                    if isinstance(decoded, Mapping):
                        for chat_id, entry in decoded.items():
                            if not isinstance(entry, Mapping):
                                continue
                            token = str(entry.get("token") or "")
                            try:
                                timestamp = int(entry.get("timestamp"))
                            except (TypeError, ValueError):
                                continue
                            if token and current - timestamp <= self.context_ttl_seconds:
                                self._contexts[str(chat_id)] = (token, timestamp)
            self._state_loaded = True

    async def _persist_contexts(self, *, now: int) -> None:
        active = {
            chat_id: {"token": token, "timestamp": timestamp}
            for chat_id, (token, timestamp) in self._contexts.items()
            if now - timestamp <= self.context_ttl_seconds
        }
        self._contexts = {
            chat_id: (entry["token"], entry["timestamp"])
            for chat_id, entry in active.items()
        }
        ciphertext = self._cipher.encrypt(
            json.dumps(active, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        await self.store.set_connector_state(
            self.channel_instance_id,
            "reply_contexts",
            {
                "version": 1,
                "token_fingerprint": self._token_fingerprint,
                "ciphertext": ciphertext,
            },
        )

    def _get_context(self, chat_id: str, *, now: int | None = None) -> str | None:
        entry = self._contexts.get(str(chat_id))
        if entry is None:
            return None
        current = int(time.time()) if now is None else int(now)
        token, timestamp = entry
        if current - timestamp > self.context_ttl_seconds:
            self._contexts.pop(str(chat_id), None)
            return None
        return token

    def _headers(self) -> dict[str, str]:
        random_uin = base64.b64encode(str(secrets.randbits(32)).encode()).decode()
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": random_uin,
            "Authorization": f"Bearer {self._bot_token}",
        }


class WechatIlinkLoginClient:
    """QR authorization helper, independent from connector lifecycle."""

    def __init__(
        self,
        http: ConnectorHttpClient | None = None,
        *,
        base_url: str = DEFAULT_WECHAT_ILINK_BASE_URL,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("WeChat login base_url must be absolute HTTPS")
        self.base_url = base_url.rstrip("/")
        self.http = http or ConnectorHttpClient("wechat-login", timeout=40.0)

    async def get_qrcode(self) -> dict[str, str]:
        response = await self.http.request_json(
            "get-qrcode", "GET",
            f"{self.base_url}/ilink/bot/get_bot_qrcode?bot_type=3",
            headers={"iLink-App-ClientVersion": "1"},
            idempotent=True,
        )
        body = _require_ilink_success("get_bot_qrcode", response.body)
        qrcode_id = str(body.get("qrcode") or "").strip()
        content = str(body.get("qrcode_img_content") or qrcode_id).strip()
        if not qrcode_id or not content:
            raise WechatIlinkError("WeChat QR response is incomplete")
        return {"qrcode_id": qrcode_id, "qrcode_content": content}

    async def poll_qrcode(self, qrcode_id: str) -> WechatLoginStatus:
        qrcode_id = str(qrcode_id or "").strip()
        if not qrcode_id:
            raise ValueError("qrcode_id is required")
        response = await self.http.request_json(
            "qrcode-status", "GET",
            f"{self.base_url}/ilink/bot/get_qrcode_status?qrcode={quote(qrcode_id, safe='')}",
            headers={"iLink-App-ClientVersion": "1"},
            idempotent=True,
        )
        body = _require_ilink_success("get_qrcode_status", response.body)
        status = str(body.get("status") or "wait")
        mapped = {"wait": "waiting", "scaned": "scanned"}.get(status, status)
        if mapped != "confirmed":
            return WechatLoginStatus(mapped)
        bot_token = str(body.get("bot_token") or "").strip()
        bot_id = str(body.get("ilink_bot_id") or "").strip()
        if not bot_token or not bot_id:
            raise WechatIlinkError("confirmed WeChat login has no bot credentials")
        return WechatLoginStatus(
            "confirmed",
            bot_token=bot_token,
            bot_id=bot_id,
            user_id=str(body.get("ilink_user_id") or "") or None,
            base_url=str(body.get("baseurl") or "") or None,
        )

    async def close(self) -> None:
        await self.http.close()


def _require_ilink_success(operation: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WechatIlinkError(f"WeChat {operation} response must be an object")
    body = dict(value)
    try:
        ret = int(body.get("ret", 0))
    except (TypeError, ValueError) as exc:
        raise WechatIlinkError(f"WeChat {operation} ret is invalid") from exc
    if ret != 0:
        message, _ = redact_secrets(str(body.get("errmsg") or "unknown error"))
        error = f"WeChat {operation} failed: ret={ret}, errcode={body.get('errcode', '')}, message={message}"
        if ret == -14 or str(body.get("errcode")) == "-14":
            raise WechatIlinkSessionExpired(error)
        raise WechatIlinkError(error)
    return body


def _extract_text(items: list[Any]) -> str:
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == _TEXT and isinstance(item.get("text_item"), Mapping):
            text = str(item["text_item"].get("text") or "")
            reference = item.get("ref_msg")
            if not isinstance(reference, Mapping):
                return text
            parts = []
            if reference.get("title"):
                parts.append(str(reference["title"]))
            ref_item = reference.get("message_item")
            if isinstance(ref_item, Mapping) and ref_item.get("type") not in {_IMAGE, _VOICE, _FILE, _VIDEO}:
                ref_text = _extract_text([ref_item])
                if ref_text:
                    parts.append(ref_text)
            return f"[引用: {' | '.join(parts)}]\n{text}" if parts else text
        if item.get("type") == _VOICE and isinstance(item.get("voice_item"), Mapping):
            if item["voice_item"].get("text"):
                return str(item["voice_item"]["text"])
    return ""


def _message_id(message: Mapping[str, Any]) -> str:
    for key in ("message_id", "msg_id", "client_id", "new_msg_id"):
        value = str(message.get(key) or "").strip()
        if value:
            return value
    stable = {key: value for key, value in message.items() if key != "context_token"}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "ilink:" + hashlib.sha256(encoded.encode()).hexdigest()


def _render_outbound_text(envelope: OutboundEnvelope) -> str:
    event = envelope.payload.get("event")
    payload = event if isinstance(event, Mapping) else envelope.payload
    for key in ("text", "message", "summary", "error", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
