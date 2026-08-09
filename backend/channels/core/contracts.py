"""Stable transport-neutral contracts for standalone Channels.

This module deliberately has no imports from Group, Bot, Workflow, database, or
platform connector code.  A Channel can therefore run and test independently;
Group-specific fields are optional until an explicit Bridge binding exists.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping


CHANNEL_PROTOCOL_VERSION = "channel.v1"
BRIDGE_PROTOCOL_VERSION = "channel-bridge.v1"
_MAX_ID_LENGTH = 512
_MAX_TEXT_LENGTH = 100_000


def canonical_message_key(channel: str, tenant: str, conversation: str, message_id: str) -> str:
    """Build an unambiguous key with length-prefixed components."""
    parts = (channel, tenant, conversation, message_id)
    return CHANNEL_PROTOCOL_VERSION + "|" + "".join(f"{len(part)}:{part}" for part in parts)


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if len(normalized) > _MAX_ID_LENGTH:
        raise ValueError(f"{field_name} exceeds {_MAX_ID_LENGTH} characters")
    return normalized


def _jsonable(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc
    return dict(value)


class BridgeDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    channel: str
    external_tenant_id: str
    external_user_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", _required(self.channel, "channel").lower())
        object.__setattr__(self, "external_tenant_id", _required(self.external_tenant_id, "external_tenant_id"))
        if self.external_user_id is not None:
            object.__setattr__(self, "external_user_id", _required(self.external_user_id, "external_user_id"))


@dataclass(frozen=True, slots=True)
class ChannelConversation:
    external_conversation_id: str
    conversation_type: str = "group"

    def __post_init__(self) -> None:
        object.__setattr__(self, "external_conversation_id", _required(self.external_conversation_id, "external_conversation_id"))
        object.__setattr__(self, "conversation_type", _required(self.conversation_type, "conversation_type").lower())


@dataclass(frozen=True, slots=True)
class InboundEnvelope:
    """A normalized external message, valid before or after Group binding."""

    identity: ChannelIdentity
    conversation: ChannelConversation
    external_message_id: str
    text: str = ""
    mentions: tuple[str, ...] = ()
    reply_to_external_id: str | None = None
    attachments: tuple[dict[str, Any], ...] = ()
    group_id: int | None = None
    member_id: int | None = None
    binding_id: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    protocol_version: str = CHANNEL_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != CHANNEL_PROTOCOL_VERSION:
            raise ValueError(f"unsupported channel protocol version: {self.protocol_version}")
        object.__setattr__(self, "external_message_id", _required(self.external_message_id, "external_message_id"))
        if len(self.text) > _MAX_TEXT_LENGTH:
            raise ValueError(f"text exceeds {_MAX_TEXT_LENGTH} characters")
        if (self.group_id is None) != (self.member_id is None):
            raise ValueError("group_id and member_id must be provided together")
        if self.group_id is not None and (self.group_id <= 0 or self.member_id <= 0):
            raise ValueError("group_id and member_id must be positive")
        if self.binding_id is not None:
            object.__setattr__(self, "binding_id", _required(self.binding_id, "binding_id"))
        normalized_mentions = tuple(_required(item, "mention") for item in self.mentions)
        object.__setattr__(self, "mentions", normalized_mentions)
        object.__setattr__(self, "attachments", tuple(_jsonable(item, "attachment") for item in self.attachments))
        object.__setattr__(self, "raw", _jsonable(self.raw, "raw"))

    @property
    def channel(self) -> str:
        return self.identity.channel

    @property
    def external_tenant_id(self) -> str:
        return self.identity.external_tenant_id

    @property
    def external_user_id(self) -> str | None:
        return self.identity.external_user_id

    @property
    def external_group_id(self) -> str:
        return self.conversation.external_conversation_id

    @property
    def reply_to_external_id_compat(self) -> str | None:
        return self.reply_to_external_id

    @property
    def idempotency_key(self) -> str:
        return canonical_message_key(self.channel, self.external_tenant_id, self.external_group_id, self.external_message_id)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["identity"] = asdict(self.identity)
        payload["conversation"] = asdict(self.conversation)
        payload["protocol_version"] = self.protocol_version
        payload["idempotency_key"] = self.idempotency_key
        return payload


@dataclass(frozen=True, slots=True)
class OutboundEnvelope:
    identity: ChannelIdentity
    conversation: ChannelConversation
    event_type: str
    payload: Mapping[str, Any]
    idempotency_key: str
    reply_to_external_id: str | None = None
    group_id: int | None = None
    session_id: str | None = None
    protocol_version: str = CHANNEL_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != CHANNEL_PROTOCOL_VERSION:
            raise ValueError(f"unsupported channel protocol version: {self.protocol_version}")
        object.__setattr__(self, "event_type", _required(self.event_type, "event_type"))
        object.__setattr__(self, "idempotency_key", _required(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "payload", _jsonable(self.payload, "payload"))
        if self.group_id is not None and self.group_id <= 0:
            raise ValueError("group_id must be positive")
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "identity": asdict(self.identity),
            "conversation": asdict(self.conversation),
        }


@dataclass(frozen=True, slots=True)
class BridgeEnvelope:
    """Only contract allowed to cross the Channel ↔ Group Bridge."""

    direction: BridgeDirection
    event_type: str
    idempotency_key: str
    payload: Mapping[str, Any]
    trace_id: str = ""
    binding_id: str | None = None
    group_id: int | None = None
    integration_member_id: int | None = None
    protocol_version: str = BRIDGE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != BRIDGE_PROTOCOL_VERSION:
            raise ValueError(f"unsupported bridge protocol version: {self.protocol_version}")
        if not isinstance(self.direction, BridgeDirection):
            object.__setattr__(self, "direction", BridgeDirection(str(self.direction)))
        object.__setattr__(self, "event_type", _required(self.event_type, "event_type"))
        object.__setattr__(self, "idempotency_key", _required(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "payload", _jsonable(self.payload, "payload"))
        if self.trace_id:
            object.__setattr__(self, "trace_id", _required(self.trace_id, "trace_id"))
        if self.binding_id is not None:
            object.__setattr__(self, "binding_id", _required(self.binding_id, "binding_id"))
        if self.group_id is not None and self.group_id <= 0:
            raise ValueError("group_id must be positive")
        if self.integration_member_id is not None and self.integration_member_id <= 0:
            raise ValueError("integration_member_id must be positive")
        if self.direction is BridgeDirection.INBOUND and self.group_id is None:
            raise ValueError("inbound BridgeEnvelope requires group_id after binding")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    channel: str
    idempotency_key: str
    status: str
    external_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", _required(self.channel, "channel").lower())
        object.__setattr__(self, "idempotency_key", _required(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "status", _required(self.status, "status").lower())
        if self.status == "sent" and not self.external_message_id:
            raise ValueError("sent delivery requires external_message_id")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
