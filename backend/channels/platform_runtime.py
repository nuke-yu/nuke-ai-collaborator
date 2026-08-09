"""Supervisor-owned lifecycle for native Feishu and WeChat connectors."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Mapping

from channels.core import InboundEnvelope, canonical_channel_instance_id

from .connectors import (
    FeishuConnector,
    FeishuWebhookResult,
    WechatIlinkConnector,
    WechatIlinkSessionExpired,
)
from .inbound_runtime import ChannelInboundService
from .inbound_runtime import ChannelInboundError
from .bridge import InboundRouteError


log = logging.getLogger(__name__)


class ChannelPlatformService:
    """Own polling ingress while delivery remains in ChannelDeliveryService."""

    def __init__(self, inbound: ChannelInboundService, *, retry_delay: float = 2.0) -> None:
        if retry_delay < 0:
            raise ValueError("platform retry_delay must not be negative")
        self.inbound = inbound
        self.retry_delay = retry_delay
        self._feishu: dict[str, FeishuConnector] = {}
        self._wechat: dict[str, WechatIlinkConnector] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping = True
        self._stats: dict[str, dict[str, object]] = {}

    def register_feishu(self, instance_id: str, connector: FeishuConnector) -> None:
        key = self._new_instance(instance_id)
        self._feishu[key] = connector
        self._stats[key] = self._initial_stats("webhook")

    def register_wechat(self, instance_id: str, connector: WechatIlinkConnector) -> None:
        key = self._new_instance(instance_id)
        self._wechat[key] = connector
        self._stats[key] = self._initial_stats("long_poll")

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks.values()):
            return
        self._stopping = False
        try:
            for instance_id, connector in self._wechat.items():
                await connector.start()
                self._tasks[instance_id] = asyncio.create_task(
                    self._poll_wechat(instance_id, connector),
                    name=f"channel-wechat-poll:{instance_id}",
                )
        except Exception:
            await self.stop()
            raise
        for instance_id in self._feishu:
            self._stats[instance_id]["up"] = True

    async def stop(self) -> None:
        self._stopping = True
        tasks, self._tasks = tuple(self._tasks.values()), {}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for stats in self._stats.values():
            stats["up"] = False

    def snapshot(self) -> dict[str, object]:
        return {
            "running": not self._stopping,
            "instances": {key: dict(value) for key, value in sorted(self._stats.items())},
        }

    async def ingest_feishu(
        self,
        instance_id: str,
        payload: Mapping[str, object],
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> FeishuWebhookResult:
        key = canonical_channel_instance_id(instance_id)
        connector = self._feishu.get(key)
        if connector is None:
            raise KeyError(f"unknown Feishu channel instance: {key}")
        result = connector.handle_webhook(payload, raw_body=raw_body, headers=headers)
        stats = self._stats[key]
        stats["last_event_at"] = int(time.time() * 1000)
        if result.envelope is not None:
            route = await self.inbound.ingest(key, result.envelope)
            field = "received" if route is not None else "ignored"
            stats[field] = int(stats[field]) + 1
        stats["up"] = True
        stats["last_error"] = None
        return result

    async def ingest_wechat(self, instance_id: str, envelope: InboundEnvelope) -> bool:
        try:
            route = await self.inbound.ingest(instance_id, envelope)
        except (ChannelInboundError, InboundRouteError):
            log.warning("ignored unauthorized WeChat conversation for %s", instance_id)
            return False
        return route is not None

    async def _poll_wechat(
        self, instance_id: str, connector: WechatIlinkConnector
    ) -> None:
        stats = self._stats[instance_id]
        failures = 0
        while not self._stopping:
            try:
                result = await connector.poll_once()
            except asyncio.CancelledError:
                raise
            except WechatIlinkSessionExpired as exc:
                stats["up"] = False
                stats["session_expired"] = True
                stats["last_error"] = str(exc)
                log.error("WeChat iLink session expired for %s", instance_id)
                return
            except Exception as exc:
                failures += 1
                stats["up"] = False
                stats["errors"] = int(stats["errors"]) + 1
                stats["last_error"] = type(exc).__name__
                log.exception("WeChat iLink poll failed for %s", instance_id)
                await asyncio.sleep(min(30.0, self.retry_delay * (2 ** min(failures - 1, 4))))
                continue
            failures = 0
            stats["up"] = True
            stats["last_error"] = None
            stats["last_poll_at"] = int(time.time() * 1000)
            stats["received"] = int(stats["received"]) + result.dispatched
            stats["ignored"] = int(stats["ignored"]) + result.ignored

    def _new_instance(self, instance_id: str) -> str:
        key = canonical_channel_instance_id(instance_id)
        if key in self._feishu or key in self._wechat:
            raise ValueError(f"duplicate native channel instance: {key}")
        return key

    @staticmethod
    def _initial_stats(mode: str) -> dict[str, object]:
        return {
            "mode": mode,
            "up": False,
            "received": 0,
            "ignored": 0,
            "errors": 0,
            "last_event_at": None,
            "last_poll_at": None,
            "last_error": None,
            "session_expired": False,
        }
