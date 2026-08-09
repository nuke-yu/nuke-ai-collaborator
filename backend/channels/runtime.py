"""Channel-owned delivery worker for connector calls and delivery audit."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence

from channels.core import (
    ChannelConversation,
    ChannelIdentity,
    DeliveryReceipt,
    OutboundEnvelope,
    canonical_channel_instance_id,
)
from channels.stores import ChannelStore, DeliveryState
from channels.bridge.group_outbox import GroupChannelOutboxRelay, GroupRelayResult
from channels.bridge.binding import ChannelBindingStore
from channels.bridge.workflow_events import WorkflowChannelProjectionRelay, WorkflowProjectionResult


log = logging.getLogger(__name__)


class ChannelConnector(Protocol):
    async def send(self, envelope: OutboundEnvelope): ...


class ChannelDeliveryError(RuntimeError):
    """A connector response cannot be accepted as a successful delivery."""


class GroupChannelRelayService:
    """Supervisor-owned lifecycle for the Group-to-Channel durable relay.

    The service only reads committed Group outboxes and writes Channel-owned
    delivery intents.  It never executes a connector and has no MCP access.
    Group discovery is injected so the service cannot accidentally open the
    central database from a Worker or Collector process.
    """

    def __init__(
        self,
        channel_store: ChannelStore,
        group_ids: Callable[[], Awaitable[Sequence[int]]],
        group_db_path: Callable[[int], str | Path],
        *,
        poll_interval: float = 1.0,
        relay_timeout: float = 10.0,
        lease_ms: int = 30_000,
        owner_id: str | None = None,
    ) -> None:
        if poll_interval <= 0 or relay_timeout <= 0 or lease_ms <= 0:
            raise ValueError("poll_interval, relay_timeout and lease_ms must be positive")
        self.channel_store = channel_store
        self.group_ids = group_ids
        self.group_db_path = group_db_path
        self.poll_interval = poll_interval
        self.relay_timeout = relay_timeout
        self.lease_ms = lease_ms
        self.owner_id = owner_id or f"supervisor-channel-relay:{uuid.uuid4()}"
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._stats = {
            "cycles": 0,
            "forwarded": 0,
            "relay_retries": 0,
            "relay_dead_letters": 0,
            "relay_lease_lost": 0,
            "projected": 0,
            "projection_retries": 0,
            "projection_dead_letters": 0,
            "errors": 0,
            "timeouts": 0,
            "last_cycle_at": None,
            "last_success_at": None,
            "relay_up": False,
            "last_error": None,
        }

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self.channel_store.initialize()
        self._stopping = False
        self._stats["relay_up"] = True
        self._task = asyncio.create_task(self._run(), name="group-channel-relay")

    async def stop(self) -> None:
        self._stopping = True
        self._stats["relay_up"] = False
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def snapshot(self) -> dict[str, object]:
        return dict(self._stats)

    async def run_once(self) -> int:
        """Relay at most one committed event per known Group."""
        ids = sorted({int(group_id) for group_id in await self.group_ids() if int(group_id) > 0})
        forwarded = 0
        cycle_healthy = True
        for group_id in ids:
            projector = WorkflowChannelProjectionRelay(
                self.group_db_path(group_id),
                ChannelBindingStore(self.channel_store.path),
                lease_ms=self.lease_ms,
                owner_id=f"{self.owner_id}:projection:{group_id}",
            )
            relay = GroupChannelOutboxRelay(
                self.group_db_path(group_id),
                self.channel_store,
                lease_ms=self.lease_ms,
                owner_id=f"{self.owner_id}:group:{group_id}",
            )
            try:
                projection_result = await asyncio.wait_for(
                    projector.run_once(group_id), timeout=self.relay_timeout
                )
                if projection_result is WorkflowProjectionResult.PROJECTED:
                    self._stats["projected"] += 1
                elif projection_result is WorkflowProjectionResult.RETRY_SCHEDULED:
                    self._stats["projection_retries"] += 1
                elif projection_result is WorkflowProjectionResult.DEAD_LETTERED:
                    self._stats["projection_dead_letters"] += 1
                result = await asyncio.wait_for(relay.relay_once(), timeout=self.relay_timeout)
            except asyncio.TimeoutError:
                cycle_healthy = False
                self._stats["timeouts"] += 1
                self._stats["last_error"] = f"relay timeout group={group_id}"
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                cycle_healthy = False
                self._stats["errors"] += 1
                self._stats["last_error"] = f"group={group_id}: {type(exc).__name__}"
                log.exception("group channel relay failed for group=%s", group_id)
                continue
            if result is GroupRelayResult.FORWARDED:
                forwarded += 1
            elif result is GroupRelayResult.RETRY_SCHEDULED:
                self._stats["relay_retries"] += 1
            elif result is GroupRelayResult.DEAD_LETTERED:
                self._stats["relay_dead_letters"] += 1
            elif result is GroupRelayResult.LEASE_LOST:
                self._stats["relay_lease_lost"] += 1
        self._stats["cycles"] += 1
        self._stats["forwarded"] += forwarded
        self._stats["last_cycle_at"] = int(time.time() * 1000)
        self._stats["relay_up"] = cycle_healthy
        if cycle_healthy:
            self._stats["last_success_at"] = self._stats["last_cycle_at"]
            self._stats["last_error"] = None
        return forwarded

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stats["errors"] += 1
                self._stats["last_error"] = type(exc).__name__
                self._stats["relay_up"] = False
                log.exception("group channel relay loop failed")
            await asyncio.sleep(self.poll_interval)


class ChannelDeliveryDispatcher:
    def __init__(self, store: ChannelStore, connector: ChannelConnector, *, channel: str | None = None, channel_instance_id: str | None = None, max_attempts: int = 3, base_delay_ms: int = 1_000, lease_ms: int = 30_000, owner_id: str | None = None):
        if max_attempts <= 0 or base_delay_ms < 0:
            raise ValueError("max_attempts must be positive and base_delay_ms must not be negative")
        self.store = store
        self.connector = connector
        self.channel = channel.lower() if channel else None
        self.channel_instance_id = canonical_channel_instance_id(channel_instance_id) if channel_instance_id else None
        self.max_attempts = max_attempts
        self.base_delay_ms = base_delay_ms
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        self.lease_ms = lease_ms
        self.owner_id = owner_id or f"channel-dispatcher:{uuid.uuid4()}"

    async def run_once(self, *, now_ms: int | None = None) -> bool:
        item = await self.store.claim_due_delivery(now_ms=now_ms, lease_owner=self.owner_id, lease_ms=self.lease_ms, channel=self.channel, channel_instance_id=self.channel_instance_id)
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
            source_event_id=item.get("source_event_id") or None,
            channel_instance_id=item.get("channel_instance_id") or None,
        )
        heartbeat = asyncio.create_task(self._heartbeat(key))
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
                details={
                    "attempt": item["attempts"],
                    "source_event_id": envelope.source_event_id,
                },
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
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def _heartbeat(self, idempotency_key: str) -> None:
        interval = max(0.01, self.lease_ms / 3 / 1000)
        while True:
            await asyncio.sleep(interval)
            if not await self.store.renew_delivery_lease(idempotency_key, self.owner_id, lease_ms=self.lease_ms):
                return


class ChannelDeliveryService:
    """Supervisor-owned dispatcher lifecycle for registered connectors."""

    def __init__(self, store: ChannelStore, *, poll_interval: float = 1.0):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self.store = store
        self.poll_interval = poll_interval
        self._connectors: dict[str, ChannelConnector] = {}
        self._dispatchers: dict[str, ChannelDeliveryDispatcher] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._stats: dict[str, object] = {
            "cycles": 0,
            "claimed": 0,
            "errors": 0,
            "last_error": None,
            "last_cycle_at": None,
            "last_success_at": None,
            "delivery_up": False,
        }

    def register(self, channel: str, connector: ChannelConnector) -> None:
        raw_key = str(channel or "").strip()
        if not raw_key:
            raise ValueError("channel is required")
        key = canonical_channel_instance_id(raw_key)
        if key in self._connectors:
            raise ValueError(f"channel connector is already registered: {key}")
        self._connectors[key] = connector
        self._dispatchers[key] = ChannelDeliveryDispatcher(
            self.store, connector, channel_instance_id=key, owner_id=f"channel-dispatcher:{key}"
        )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self.store.initialize()
        self.require_registered_instances(await self.store.list_open_delivery_instances())
        self._stopping = False
        self._stats["delivery_up"] = True
        self._task = asyncio.create_task(self._run(), name="channel-delivery")

    async def stop(self) -> None:
        self._stopping = True
        self._stats["delivery_up"] = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for connector in tuple(self._connectors.values()):
            close = getattr(connector, "close", None)
            if close is None:
                continue
            try:
                await close()
            except Exception:
                log.exception("failed to close channel connector")

    def snapshot(self) -> dict[str, object]:
        return {
            **self._stats,
            "running": self._task is not None and not self._task.done(),
            "registered_channels": sorted(self._connectors),
        }

    def require_registered_instances(self, instance_ids: Sequence[str]) -> None:
        required = {
            canonical_channel_instance_id(value)
            for value in instance_ids
            if str(value or "").strip()
        }
        missing = sorted(required.difference(self._connectors))
        if missing:
            raise RuntimeError(f"active channel bindings have no connector: {', '.join(missing)}")

    async def run_once(self) -> int:
        claimed = 0
        cycle_healthy = True
        for dispatcher in tuple(self._dispatchers.values()):
            try:
                claimed += int(await dispatcher.run_once())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                cycle_healthy = False
                self._stats["errors"] += 1
                self._stats["last_error"] = type(exc).__name__
                log.exception("channel delivery dispatcher failed")
        self._stats["cycles"] += 1
        self._stats["claimed"] += claimed
        self._stats["last_cycle_at"] = int(time.time() * 1000)
        self._stats["delivery_up"] = cycle_healthy
        if cycle_healthy:
            self._stats["last_success_at"] = self._stats["last_cycle_at"]
            self._stats["last_error"] = None
        return claimed

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stats["errors"] += 1
                self._stats["last_error"] = type(exc).__name__
                self._stats["delivery_up"] = False
                log.exception("channel delivery service loop failed")
            await asyncio.sleep(self.poll_interval)
