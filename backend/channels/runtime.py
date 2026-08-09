"""Channel-owned delivery worker for connector calls and delivery audit."""
from __future__ import annotations

import time
import uuid
from typing import Protocol

from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope
from channels.stores import ChannelStore, DeliveryState


class ChannelConnector(Protocol):
    async def send(self, envelope: OutboundEnvelope): ...


class ChannelDeliveryError(RuntimeError):
    """A connector response cannot be accepted as a successful delivery."""


class ChannelDeliveryDispatcher:
    def __init__(self, store: ChannelStore, connector: ChannelConnector, *, max_attempts: int = 3, base_delay_ms: int = 1_000, lease_ms: int = 30_000, owner_id: str | None = None):
        if max_attempts <= 0 or base_delay_ms < 0:
            raise ValueError("max_attempts must be positive and base_delay_ms must not be negative")
        self.store = store
        self.connector = connector
        self.max_attempts = max_attempts
        self.base_delay_ms = base_delay_ms
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        self.lease_ms = lease_ms
        self.owner_id = owner_id or f"channel-dispatcher:{uuid.uuid4()}"

    async def run_once(self, *, now_ms: int | None = None) -> bool:
        item = await self.store.claim_due_delivery(now_ms=now_ms, lease_owner=self.owner_id, lease_ms=self.lease_ms)
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
            if not isinstance(receipt, DeliveryReceipt):
                raise ChannelDeliveryError("connector returned an invalid delivery receipt")
            if receipt.channel != envelope.identity.channel:
                raise ChannelDeliveryError("delivery receipt channel mismatch")
            if receipt.idempotency_key != envelope.idempotency_key:
                raise ChannelDeliveryError("delivery receipt idempotency key mismatch")
            if receipt.status != "sent" or not receipt.external_message_id:
                detail = receipt.error_message or receipt.error_code or f"connector returned status={receipt.status}"
                raise ChannelDeliveryError(detail)
            if not await self.store.transition_delivery_with_audit(
                key,
                DeliveryState.SENT,
                event_type="delivery.sent",
                details={"attempt": item["attempts"]},
                external_message_id=receipt.external_message_id,
                lease_owner=self.owner_id,
            ):
                raise ChannelDeliveryError("delivery state changed before success could be recorded")
        except Exception as exc:
            attempt = int(item["attempts"])
            retry_at = None
            if attempt < self.max_attempts:
                current = now_ms if now_ms is not None else int(time.time() * 1000)
                retry_at = current + self.base_delay_ms * (2 ** (attempt - 1))
            await self.store.transition_delivery_with_audit(
                key,
                DeliveryState.RETRYING if retry_at is not None else DeliveryState.DEAD_LETTER,
                event_type="delivery.retrying" if retry_at is not None else "delivery.dead_letter",
                details={"attempt": attempt, "error": str(exc), "retry_at_ms": retry_at},
                error=str(exc),
                retry_at_ms=retry_at,
                lease_owner=self.owner_id,
            )
        return True
