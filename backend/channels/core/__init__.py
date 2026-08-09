"""Transport-neutral Channel and Channel-Group Bridge contracts."""

from .contracts import (
    BRIDGE_PROTOCOL_VERSION,
    CHANNEL_PROTOCOL_VERSION,
    BridgeDirection,
    BridgeEnvelope,
    ChannelConversation,
    ChannelIdentity,
    canonical_message_key,
    delivery_projection_id,
    DeliveryReceipt,
    InboundEnvelope,
    OutboundEnvelope,
)

__all__ = [
    "BRIDGE_PROTOCOL_VERSION",
    "CHANNEL_PROTOCOL_VERSION",
    "BridgeDirection",
    "BridgeEnvelope",
    "ChannelConversation",
    "ChannelIdentity",
    "canonical_message_key",
    "delivery_projection_id",
    "DeliveryReceipt",
    "InboundEnvelope",
    "OutboundEnvelope",
]
