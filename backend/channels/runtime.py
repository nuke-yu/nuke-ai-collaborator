"""Channel-owned delivery worker for connector calls and delivery audit."""
from __future__ import annotations

import time
from typing import Protocol

from channels.core import ChannelConversation, ChannelIdentity, OutboundEnvelope
from channels.stores import ChannelStore


class ChannelConnector(Protocol):
    async def send(self, envelope: OutboundEnvelope): ...


class ChannelDeliveryDispatcher:
    def __init__(self, store: ChannelStore, connector: ChannelConnector, *, max_attempts: int = 3, base_delay_ms: int = 1_000):
        if max_attempts <= 0 or base_delay_ms < 0:
            raise ValueError("max_attempts must be positive and base_delay_ms must not be negative")
        self.store = store
        self.connector = connector
        self.max_attempts = max_attempts
        self.base_delay_ms = base_delay_ms

    async def run_once(self, *, now_ms: int | None = None) -> bool:
        item = await self.store.claim_due_delivery(now_ms=now_ms)
        if item is None:
            return False
        key = item["idempotency_key"]
        envelope = OutboundEnvelope(
            identity=ChannelIdentity(item["channel"], item["external_tenant_id"]),
            conversation=ChannelConversation(item["external_conversation_id"]),
            event_type=item["event_type"],
            payload=item["payload"],
            idempotency_key=key,
            reply_to_external_id=item["reply_to_external_id"],
            group_id=item["group_id"],
            session_id=item["session_id"],
        )
        try:
            receipt = await self.connector.send(envelope)
            await self.store.mark_sent(key, receipt.external_message_id or "")
            await self.store.record_audit(key, "delivery.sent", {"attempt": item["attempts"]})
        except Exception as exc:
            attempt = int(item["attempts"])
            retry_at = None
            if attempt < self.max_attempts:
                current = now_ms if now_ms is not None else int(time.time() * 1000)
                retry_at = current + self.base_delay_ms * (2 ** (attempt - 1))
            await self.store.mark_failed(key, str(exc), retry_at_ms=retry_at)
            await self.store.record_audit(key, "delivery.retrying" if retry_at is not None else "delivery.dead_letter", {
                "attempt": attempt,
                "error": str(exc),
                "retry_at_ms": retry_at,
            })
        return True
