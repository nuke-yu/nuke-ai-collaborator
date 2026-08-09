"""Channel-neutral inbound adapter contract for external webhooks."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Awaitable, Callable, Mapping

from channels.core import ChannelConversation, ChannelIdentity, InboundEnvelope


class ChannelAuthError(PermissionError):
    pass


class ChannelAdapter:
    """Normalize a signed webhook and dispatch it exactly once per message."""

    def __init__(
        self,
        *,
        channel: str,
        secret: str,
        resolve_group: Callable[[str, str], Awaitable[tuple[int, int] | None]],
        register_attachment: Callable[[int, dict[str, Any]], Awaitable[str]] | None = None,
        record_inbound: Callable[[InboundEnvelope], Awaitable[bool]] | None = None,
    ) -> None:
        if not channel.strip() or not secret:
            raise ValueError("channel and secret are required")
        self.channel = channel.strip().lower()
        self.secret = secret.encode()
        self.resolve_group = resolve_group
        self.register_attachment = register_attachment
        self.record_inbound = record_inbound

    def verify_signature(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        supplied = signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, supplied)

    async def normalize(self, payload: Mapping[str, Any], *, signature: str = "") -> InboundEnvelope | None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if not self.verify_signature(raw, signature):
            raise ChannelAuthError("invalid channel signature")
        tenant = str(payload.get("tenant_id") or "").strip()
        external_group = str(payload.get("group_id") or "").strip()
        external_user = str(payload.get("user_id") or "").strip()
        message_id = str(payload.get("message_id") or "").strip()
        if not all((tenant, external_group, external_user, message_id)):
            raise ValueError("tenant_id, group_id, user_id, and message_id are required")
        resolved = await self.resolve_group(tenant, external_group)
        if resolved is None:
            raise ChannelAuthError("external group is not authorized")
        group_id, member_id = resolved
        mentions = tuple(str(item).strip() for item in (payload.get("mentions") or ()) if str(item).strip())
        attachments = tuple(item for item in (payload.get("attachments") or ()) if isinstance(item, dict))
        envelope = InboundEnvelope(
            identity=ChannelIdentity(self.channel, tenant, external_user),
            conversation=ChannelConversation(external_group),
            external_message_id=message_id,
            text=str(payload.get("text") or ""),
            group_id=int(group_id),
            member_id=int(member_id),
            mentions=mentions,
            reply_to_external_id=str(payload["reply_to_id"]) if payload.get("reply_to_id") else None,
            attachments=attachments,
            raw=payload,
        )
        if self.record_inbound is not None and not await self.record_inbound(envelope):
            return None
        return envelope

    async def register_attachments(self, envelope: InboundEnvelope) -> tuple[str, ...]:
        if self.register_attachment is None:
            return ()
        return tuple(
            await self.register_attachment(envelope.group_id, attachment)
            for attachment in envelope.attachments
        )
