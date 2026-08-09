"""Project Group events into standalone Channel delivery envelopes."""
from __future__ import annotations

from typing import Any, Mapping

from channels.core import BridgeDirection, BridgeEnvelope, ChannelConversation, ChannelIdentity, OutboundEnvelope, delivery_projection_id

from .binding import BindingStatus, ChannelBinding


class OutboundPolicyError(PermissionError):
    """The binding does not permit this Group event to leave through the Channel."""


_DEFAULT_EVENTS = frozenset({
    "workflow.completed",
    "workflow.failed",
    "permission_requested",
    "artifact_produced",
    "session_recovered",
    "task_stuck",
})


class OutboundEventProjector:
    """Keep Group event semantics at the Bridge and Channel transport boundary."""

    def __init__(self, binding: ChannelBinding):
        if binding.status is not BindingStatus.ACTIVE:
            raise OutboundPolicyError("only active channel bindings can emit events")
        self.binding = binding

    def project(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        event_id: str | None = None,
        session_id: str | None = None,
        reply_to_external_id: str | None = None,
        trace_id: str = "",
    ) -> OutboundEnvelope:
        event_type = str(event_type or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        allowed = self.binding.outbound_policy.get("events")
        allowed_events = set(allowed) if isinstance(allowed, (list, tuple, set)) else _DEFAULT_EVENTS
        if event_type not in allowed_events:
            raise OutboundPolicyError(f"event is not allowed by binding: {event_type}")
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be an object")
        if event_id is None or not str(event_id).strip():
            raise ValueError("canonical event_id is required for outbound delivery")
        source_event_id = str(event_id).strip()
        key = delivery_projection_id(source_event_id, self.binding.binding_id)
        channel = self.binding.channel_instance_id.split(":", 1)[0]
        bridge = BridgeEnvelope(
            direction=BridgeDirection.OUTBOUND,
            event_type=event_type,
            idempotency_key=key,
            payload={
                "event": dict(payload),
                "binding_id": self.binding.binding_id,
                "config_version": self.binding.config_version,
            },
            trace_id=trace_id,
            binding_id=self.binding.binding_id,
            group_id=self.binding.group_id,
        )
        return OutboundEnvelope(
            identity=ChannelIdentity(channel, self.binding.external_tenant_id),
            conversation=ChannelConversation(self.binding.external_conversation_id),
            event_type=bridge.event_type,
            payload=bridge.payload,
            idempotency_key=bridge.idempotency_key,
            reply_to_external_id=reply_to_external_id,
            group_id=self.binding.group_id,
            session_id=session_id,
            source_event_id=source_event_id,
        )
