"""Reference signed-webhook Connector.

It is intentionally platform-neutral.  A vendor Connector can implement the
same shape later without importing Group or Bridge internals.
"""
from __future__ import annotations

import hashlib
import hmac
import json
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
    ) -> None:
        if not channel.strip() or not secret:
            raise ValueError("channel and secret are required")
        self.channel = channel.strip().lower()
        self._secret = secret.encode()
        self._send = send

    def signature(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def verify_signature(self, body: bytes, supplied: str) -> bool:
        expected = self.signature(body)
        return hmac.compare_digest(expected, str(supplied or "").removeprefix("sha256="))

    def _body(self, payload: Mapping[str, Any]) -> bytes:
        try:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise ConnectorError("webhook payload must be JSON serializable") from exc

    async def normalize(self, payload: Mapping[str, Any], *, signature: str) -> InboundEnvelope:
        body = self._body(payload)
        if not self.verify_signature(body, signature):
            raise ConnectorAuthError("invalid webhook signature")
        tenant = str(payload.get("tenant_id") or "").strip()
        conversation = str(payload.get("group_id") or payload.get("conversation_id") or "").strip()
        user = str(payload.get("user_id") or "").strip()
        message_id = str(payload.get("message_id") or "").strip()
        if not all((tenant, conversation, user, message_id)):
            raise ConnectorError("tenant_id, conversation_id, user_id, and message_id are required")
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
