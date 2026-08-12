"""Reference signed-webhook Connector.

It is intentionally platform-neutral.  A vendor Connector can implement the
same shape later without importing Group or Bridge internals.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import asyncio
import time
from typing import Any, Awaitable, Callable, Mapping

from channels.core import (
    ChannelConversation,
    ChannelIdentity,
    DeliveryReceipt,
    InboundEnvelope,
    OutboundEnvelope,
)


class ConnectorError(RuntimeError):
    """A platform transport or payload failure."""


class ConnectorAuthError(ConnectorError):
    """A webhook signature or tenant authentication failure."""


class SignedWebhookConnector:
    """Normalize signed inbound webhooks and send outbound envelopes."""

    def __init__(
        self,
        *,
        channel: str,
        secret: str,
        send: Callable[[OutboundEnvelope], Awaitable[str]] | None = None,
        replay_guard: Callable[[str, int], Awaitable[bool]] | None = None,
        replay_window_seconds: int = 300,
        allow_in_memory_replay_guard: bool = False,
    ) -> None:
        if not channel.strip() or not secret:
            raise ValueError("channel and secret are required")
        self.channel = channel.strip().lower()
        self._secret = secret.encode()
        self._send = send
        if replay_window_seconds <= 0:
            raise ValueError("replay_window_seconds must be positive")
        self._replay_guard = replay_guard
        self._allow_in_memory_replay_guard = allow_in_memory_replay_guard
        self._replay_window_seconds = replay_window_seconds
        self._replay_seen: dict[str, int] = {}
        self._replay_lock = asyncio.Lock()

    def signature(self, body: bytes, timestamp: str | int) -> str:
        return hmac.new(self._secret, _signed_payload(timestamp, body), hashlib.sha256).hexdigest()

    def verify_signature(self, body: bytes, supplied: str, timestamp: str | int) -> bool:
        expected = self.signature(body, timestamp)
        return hmac.compare_digest(expected, str(supplied or "").removeprefix("sha256="))

    async def normalize(
        self,
        payload: Mapping[str, Any] | bytes,
        *,
        signature: str,
        raw_body: bytes | None = None,
        timestamp: str | int | None = None,
        now: int | None = None,
    ) -> InboundEnvelope:
        if isinstance(payload, bytes):
            body = payload
            try:
                payload = json.loads(body.decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConnectorError("webhook body must be valid JSON") from exc
        else:
            if raw_body is None:
                raise ConnectorError("raw_body is required for webhook signature verification")
            body = raw_body
        if not isinstance(payload, Mapping):
            raise ConnectorError("webhook payload must be an object")
        timestamp_value = _parse_timestamp(timestamp)
        if not self.verify_signature(body, signature, timestamp_value):
            raise ConnectorAuthError("invalid webhook signature")
        current = int(time.time()) if now is None else int(now)
        if abs(current - timestamp_value) > self._replay_window_seconds:
            raise ConnectorAuthError("webhook timestamp is outside replay window")
        tenant = str(payload.get("tenant_id") or "").strip()
        conversation = str(payload.get("group_id") or payload.get("conversation_id") or "").strip()
        user = str(payload.get("user_id") or "").strip()
        message_id = str(payload.get("message_id") or "").strip()
        if not all((tenant, conversation, user, message_id)):
            raise ConnectorError("tenant_id, conversation_id, user_id, and message_id are required")
        # Prefer the platform event identity over a timestamp/body hash. This
        # makes the durable guard resilient to clock skew and permits a 24h
        # uniqueness window in ChannelStore.
        replay_key = f"{self.channel}:{tenant}:{message_id}"
        if self._replay_guard is not None:
            accepted = await self._replay_guard(replay_key, timestamp_value)
        elif self._allow_in_memory_replay_guard:
            async with self._replay_lock:
                self._replay_seen = {key: value for key, value in self._replay_seen.items() if current - value <= self._replay_window_seconds}
                accepted = replay_key not in self._replay_seen
                if accepted:
                    self._replay_seen[replay_key] = current
        else:
            raise ConnectorAuthError("durable replay guard is required")
        if not accepted:
            raise ConnectorAuthError("webhook replay detected")
        mentions = tuple(str(item).strip() for item in (payload.get("mentions") or ()) if str(item).strip())
        attachments = tuple(item for item in (payload.get("attachments") or ()) if isinstance(item, Mapping))
        return InboundEnvelope(
            identity=ChannelIdentity(self.channel, tenant, user),
            conversation=ChannelConversation(conversation, str(payload.get("conversation_type") or "group")),
            external_message_id=message_id,
            text=str(payload.get("text") or ""),
            mentions=mentions,
            reply_to_external_id=str(payload["reply_to_id"]) if payload.get("reply_to_id") else None,
            attachments=attachments,
            raw=payload,
        )

    async def send(self, envelope: OutboundEnvelope) -> DeliveryReceipt:
        if envelope.identity.channel != self.channel:
            raise ConnectorError("outbound envelope channel does not match connector")
        if self._send is None:
            raise ConnectorError("webhook connector has no outbound transport")
        try:
            external_message_id = await self._send(envelope)
        except Exception as exc:
            raise ConnectorError(f"outbound webhook failed: {exc}") from exc
        if not str(external_message_id or "").strip():
            raise ConnectorError("outbound transport returned an empty message id")
        return DeliveryReceipt(
            channel=self.channel,
            idempotency_key=envelope.idempotency_key,
            status="sent",
            external_message_id=str(external_message_id),
        )


def _parse_timestamp(value: str | int | None) -> int:
    try:
        timestamp = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ConnectorAuthError("webhook timestamp is required") from exc
    if timestamp <= 0:
        raise ConnectorAuthError("webhook timestamp is invalid")
    return timestamp


def _signed_payload(timestamp: str | int, body: bytes) -> bytes:
    return f"{int(str(timestamp))}.".encode("ascii") + body
