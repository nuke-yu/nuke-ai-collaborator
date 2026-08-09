"""Inbound Channel → configured Bot routing at the Bridge boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from channels.core import BridgeDirection, BridgeEnvelope, InboundEnvelope

from .binding import BindingStatus, ChannelBinding


class InboundRouteError(PermissionError):
    """The external message cannot be routed through the current binding."""


def _mention_key(value: str) -> str:
    return str(value or "").strip().removeprefix("@").casefold()


@dataclass(frozen=True, slots=True)
class InboundRoute:
    binding_id: str
    group_id: int
    target_bot_id: int
    bridge_envelope: BridgeEnvelope


class InboundBotRouter:
    """Resolve a normalized message into one allowed Bot without dispatching it."""

    def __init__(self, binding: ChannelBinding, *, integration_member_id: int):
        if binding.status is not BindingStatus.ACTIVE:
            raise InboundRouteError("only active channel bindings can route messages")
        if integration_member_id <= 0:
            raise ValueError("integration_member_id must be positive")
        self.binding = binding
        self.integration_member_id = integration_member_id

    def route(self, envelope: InboundEnvelope, *, bot_mentions: Mapping[str, int] | None = None) -> InboundRoute:
        if envelope.channel != self.binding.channel_instance_id.split(":", 1)[0]:
            raise InboundRouteError("message channel does not match binding")
        if envelope.external_tenant_id != self.binding.external_tenant_id:
            raise InboundRouteError("message tenant does not match binding")
        if envelope.external_group_id != self.binding.external_conversation_id:
            raise InboundRouteError("message conversation does not match binding")
        if envelope.group_id is not None and envelope.group_id != self.binding.group_id:
            raise InboundRouteError("message Group does not match binding")

        normalized_mentions = {
            _mention_key(key): int(value)
            for key, value in (bot_mentions or {}).items()
        }
        mentioned_ids = [normalized_mentions[_mention_key(item)] for item in envelope.mentions if _mention_key(item) in normalized_mentions]
        mentioned_ids = list(dict.fromkeys(mentioned_ids))
        if len(mentioned_ids) > 1:
            raise InboundRouteError("message mentions more than one Bot")
        if self.binding.mention_required and not mentioned_ids:
            raise InboundRouteError("a Bot mention is required for this Channel binding")
        target_bot_id = mentioned_ids[0] if mentioned_ids else self.binding.default_bot_id
        if target_bot_id not in self.binding.allowed_bot_ids:
            raise InboundRouteError("target Bot is not allowed by the Channel binding")

        bridge = BridgeEnvelope(
            direction=BridgeDirection.INBOUND,
            event_type="message.received",
            idempotency_key=envelope.idempotency_key,
            payload={
                "channel": envelope.channel,
                "external_tenant_id": envelope.external_tenant_id,
                "external_conversation_id": envelope.external_group_id,
                "external_user_id": envelope.external_user_id,
                "external_message_id": envelope.external_message_id,
                "text": envelope.text,
                "mentions": list(envelope.mentions),
                "attachments": list(envelope.attachments),
                "reply_to_external_id": envelope.reply_to_external_id,
                "target_bot_id": target_bot_id,
            },
            binding_id=self.binding.binding_id,
            group_id=self.binding.group_id,
            integration_member_id=self.integration_member_id,
        )
        return InboundRoute(self.binding.binding_id, self.binding.group_id, target_bot_id, bridge)

    async def dispatch(
        self,
        envelope: InboundEnvelope,
        dispatch_bridge: Callable[[BridgeEnvelope], Awaitable[None]],
        *,
        bot_mentions: Mapping[str, int] | None = None,
    ) -> InboundRoute:
        route = self.route(envelope, bot_mentions=bot_mentions)
        await dispatch_bridge(route.bridge_envelope)
        return route
