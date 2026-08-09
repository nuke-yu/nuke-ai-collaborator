"""Supervisor-owned ingress from platform Connectors into Group Workers."""
from __future__ import annotations

from dataclasses import replace
from typing import Awaitable, Callable

from channels.bridge import (
    ChannelBindingStore,
    InboundBotRouter,
    InboundRoute,
    IntegrationMember,
    IntegrationMemberStore,
)
from channels.core import InboundEnvelope, canonical_channel_instance_id
from channels.stores import ChannelStore


class ChannelInboundError(PermissionError):
    """An inbound platform message cannot cross the Channel–Group boundary."""


class ChannelInboundService:
    def __init__(
        self,
        channel_store: ChannelStore,
        binding_store: ChannelBindingStore,
        member_store: IntegrationMemberStore,
        dispatch: Callable[[InboundRoute, IntegrationMember], Awaitable[None]],
    ) -> None:
        self.channel_store = channel_store
        self.binding_store = binding_store
        self.member_store = member_store
        self.dispatch = dispatch

    async def ingest(
        self,
        channel_instance_id: str,
        envelope: InboundEnvelope,
    ) -> InboundRoute | None:
        instance_id = canonical_channel_instance_id(channel_instance_id)
        if envelope.channel != instance_id.split(":", 1)[0]:
            raise ChannelInboundError("inbound channel does not match channel instance")
        binding = await self.binding_store.resolve_active(
            instance_id,
            envelope.external_tenant_id,
            envelope.external_group_id,
        )
        if binding is None:
            raise ChannelInboundError("external conversation has no active Channel binding")
        member = await self.member_store.get_for_binding(binding.binding_id)
        if member is None:
            raise ChannelInboundError("active Channel binding has no active Integration Member")
        bound = replace(
            envelope,
            group_id=binding.group_id,
            member_id=member.integration_member_id,
            binding_id=binding.binding_id,
        )
        route = InboundBotRouter(
            binding,
            integration_member_id=member.integration_member_id,
        ).route(bound, bot_mentions=_bot_mentions(binding.inbound_policy))
        if not await self.channel_store.prepare_inbound(
            bound, channel_instance_id=instance_id
        ):
            return None
        await self.dispatch(route, member)
        if not await self.channel_store.mark_inbound_dispatched(bound.idempotency_key):
            raise RuntimeError("Channel inbound dispatch state changed unexpectedly")
        return route


def _bot_mentions(policy) -> dict[str, int]:
    configured = policy.get("bot_mentions") if isinstance(policy, dict) else None
    if not isinstance(configured, dict):
        return {}
    mentions: dict[str, int] = {}
    for key, value in configured.items():
        try:
            bot_id = int(value)
        except (TypeError, ValueError):
            continue
        if str(key or "").strip() and bot_id > 0:
            mentions[str(key)] = bot_id
    return mentions
