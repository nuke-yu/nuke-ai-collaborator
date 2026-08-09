"""Production bootstrap for configured Channel connector processes."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .core import canonical_channel_instance_id
from .process import ChannelProcessClient, ChannelProcessManifest
from .runtime import ChannelDeliveryService
from .secrets import ChannelSecretResolver


class ChannelConnectorConfigError(ValueError):
    """A deployment connector descriptor is invalid or incomplete."""


def configure_process_connectors(
    service: ChannelDeliveryService,
    raw_config: str | None,
    *,
    secret_resolver: ChannelSecretResolver | None = None,
) -> tuple[str, ...]:
    """Register process-backed connectors from a secret-free JSON descriptor.

    Secret values are never accepted in the descriptor. ``env_keys`` only names
    variables that the explicit resolver may pass to the connector process.
    """
    try:
        descriptors = json.loads(raw_config or "[]")
    except json.JSONDecodeError as exc:
        raise ChannelConnectorConfigError("NUKE_CHANNEL_CONNECTORS_JSON must be valid JSON") from exc
    if not isinstance(descriptors, list):
        raise ChannelConnectorConfigError("channel connector config must be a list")

    registered: list[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            raise ChannelConnectorConfigError("each channel connector config must be an object")
        unknown = set(descriptor).difference({
            "channel_instance_id", "argv", "version", "max_seconds",
            "max_frame_bytes", "env_keys",
        })
        if unknown:
            raise ChannelConnectorConfigError(
                f"unsupported channel connector config fields: {', '.join(sorted(map(str, unknown)))}"
            )
        raw_instance_id = str(descriptor.get("channel_instance_id") or "").strip()
        argv = descriptor.get("argv")
        if not raw_instance_id or not isinstance(argv, list) or not argv:
            raise ChannelConnectorConfigError("channel_instance_id and non-empty argv are required")
        instance_id = canonical_channel_instance_id(raw_instance_id)
        if any(not isinstance(value, str) or not value.strip() for value in argv):
            raise ChannelConnectorConfigError("connector argv entries must be non-empty strings")
        if instance_id in registered:
            raise ChannelConnectorConfigError(f"duplicate channel connector instance: {instance_id}")
        env_keys = descriptor.get("env_keys", [])
        if not isinstance(env_keys, list):
            raise ChannelConnectorConfigError("connector env_keys must be a list")
        manifest = ChannelProcessManifest(
            channel_instance_id=instance_id,
            version=str(descriptor.get("version") or "1"),
            max_seconds=_positive_number(descriptor, "max_seconds", 30.0),
            max_frame_bytes=int(_positive_number(descriptor, "max_frame_bytes", 256_000)),
            env_keys=tuple(env_keys),
        )
        service.register(
            instance_id,
            ChannelProcessClient(list(argv), manifest, secret_resolver=secret_resolver),
        )
        registered.append(instance_id)
    return tuple(registered)


def _positive_number(descriptor: Mapping[str, Any], name: str, default: float) -> float:
    try:
        value = float(descriptor.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ChannelConnectorConfigError(f"connector {name} must be numeric") from exc
    if value <= 0:
        raise ChannelConnectorConfigError(f"connector {name} must be positive")
    return value
