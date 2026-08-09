"""Production bootstrap for configured Channel connector processes."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .core import canonical_channel_instance_id
from .process import ChannelProcessClient, ChannelProcessManifest
from .runtime import ChannelDeliveryService
from .secrets import ChannelSecretResolver, EnvironmentSecretResolver, validate_env_names
from .stores import ChannelStore
from .inbound_runtime import ChannelInboundService
from .platform_runtime import ChannelPlatformService
from .connectors import FeishuConnector, WechatIlinkConnector


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


def configure_platform_connectors(
    service: ChannelDeliveryService,
    store: ChannelStore,
    inbound: ChannelInboundService,
    raw_config: str | None,
    *,
    secret_resolver: ChannelSecretResolver | None = None,
) -> ChannelPlatformService:
    """Register native Feishu/personal-WeChat descriptors without inline secrets."""
    try:
        descriptors = json.loads(raw_config or "[]")
    except json.JSONDecodeError as exc:
        raise ChannelConnectorConfigError(
            "NUKE_CHANNEL_PLATFORMS_JSON must be valid JSON"
        ) from exc
    if not isinstance(descriptors, list):
        raise ChannelConnectorConfigError("native channel config must be a list")
    resolver = secret_resolver or EnvironmentSecretResolver()
    platforms = ChannelPlatformService(inbound)
    registered: set[str] = set()
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            raise ChannelConnectorConfigError("each native channel config must be an object")
        kind = str(descriptor.get("type") or "").strip().lower()
        raw_instance_id = str(descriptor.get("channel_instance_id") or "").strip()
        if not raw_instance_id:
            raise ChannelConnectorConfigError("native connector channel_instance_id is required")
        instance_id = canonical_channel_instance_id(raw_instance_id)
        if instance_id in registered:
            raise ChannelConnectorConfigError(f"duplicate native channel instance: {instance_id}")
        registered.add(instance_id)
        if kind == "feishu":
            _reject_unknown(descriptor, {
                "type", "channel_instance_id", "app_id_env", "app_secret_env",
                "verification_token_env", "encrypt_key_env", "region",
            })
            values = _resolve_required(
                resolver, instance_id, descriptor,
                ("app_id_env", "app_secret_env", "verification_token_env"),
                optional=("encrypt_key_env",),
            )
            connector = FeishuConnector(
                channel_instance_id=instance_id,
                app_id=values["app_id_env"],
                app_secret=values["app_secret_env"],
                verification_token=values["verification_token_env"],
                encrypt_key=values.get("encrypt_key_env"),
                region=str(descriptor.get("region") or "feishu_cn"),
            )
            service.register(instance_id, connector)
            platforms.register_feishu(instance_id, connector)
        elif kind in {"wechat", "wechat_ilink"}:
            _reject_unknown(descriptor, {
                "type", "channel_instance_id", "bot_id_env", "bot_token_env", "base_url",
            })
            values = _resolve_required(
                resolver, instance_id, descriptor, ("bot_id_env", "bot_token_env")
            )
            connector = WechatIlinkConnector(
                channel_instance_id=instance_id,
                bot_id=values["bot_id_env"],
                bot_token=values["bot_token_env"],
                store=store,
                on_inbound=platforms.ingest_wechat,
                base_url=str(descriptor.get("base_url") or "https://ilinkai.weixin.qq.com"),
            )
            service.register(instance_id, connector)
            platforms.register_wechat(instance_id, connector)
        else:
            raise ChannelConnectorConfigError(f"unsupported native channel type: {kind or '<empty>'}")
    return platforms


def _resolve_required(
    resolver: ChannelSecretResolver,
    instance_id: str,
    descriptor: Mapping[str, Any],
    required: tuple[str, ...],
    *,
    optional: tuple[str, ...] = (),
) -> dict[str, str]:
    fields = required + optional
    env_names: list[str] = []
    field_names: dict[str, str] = {}
    for field in fields:
        value = str(descriptor.get(field) or "").strip()
        if field in required and not value:
            raise ChannelConnectorConfigError(f"native connector requires {field}")
        if value:
            field_names[field] = value
            env_names.append(value)
    validate_env_names(env_names)
    resolved = resolver.resolve(instance_id, env_names)
    missing = [field_names[field] for field in required if field_names[field] not in resolved]
    if missing:
        raise ChannelConnectorConfigError(
            f"native connector environment is missing: {', '.join(missing)}"
        )
    return {
        field: str(resolved[env_name])
        for field, env_name in field_names.items()
        if env_name in resolved
    }


def _reject_unknown(descriptor: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(descriptor).difference(allowed)
    if unknown:
        raise ChannelConnectorConfigError(
            f"unsupported native channel config fields: {', '.join(sorted(map(str, unknown)))}"
        )
