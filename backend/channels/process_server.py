"""Server side of the Channel JSONL process boundary.

The server accepts only versioned BridgeEnvelope frames and returns typed
DeliveryReceipt values.  Connector implementations stay behind the injected
handler, so this module is usable by a channel-specific process without any
Group or Supervisor imports.
"""
from __future__ import annotations

import asyncio
import json
from typing import Protocol

from executors.redaction import redact_secrets

from .core import (
    BridgeDirection,
    BridgeEnvelope,
    ChannelConversation,
    ChannelIdentity,
    DeliveryReceipt,
    OutboundEnvelope,
)
from .process import ChannelProcessError, ChannelProcessManifest


class ChannelProcessHandler(Protocol):
    async def send(self, envelope: OutboundEnvelope) -> DeliveryReceipt: ...


class ChannelProcessServer:
    def __init__(self, manifest: ChannelProcessManifest, handler: ChannelProcessHandler):
        self.manifest = manifest
        self.handler = handler

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve until EOF; each request is handled serially for protocol order."""
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                if len(raw) > self.manifest.max_frame_bytes:
                    await self._write_error(writer, "", "request frame exceeds limit")
                    return
                request_id = ""
                try:
                    frame = json.loads(raw.decode("utf-8"))
                    request_id = str(frame.get("request_id") or "") if isinstance(frame, dict) else ""
                    receipt = await self._handle(frame)
                    await self._write(writer, {"request_id": request_id, "receipt": receipt.to_dict()})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._write_error(writer, request_id, _safe_error(exc))
        finally:
            writer.close()
            wait_closed = getattr(writer, "wait_closed", None)
            if callable(wait_closed):
                await wait_closed()

    async def _handle(self, frame: object) -> DeliveryReceipt:
        if not isinstance(frame, dict):
            raise ChannelProcessError("request must be an object")
        manifest = frame.get("manifest")
        if not isinstance(manifest, dict) or manifest.get("channel_instance_id") != self.manifest.channel_instance_id or manifest.get("version") != self.manifest.version:
            raise ChannelProcessError("channel process manifest mismatch")
        bridge_data = frame.get("bridge")
        if not isinstance(bridge_data, dict):
            raise ChannelProcessError("request must contain bridge envelope")
        bridge = BridgeEnvelope(**bridge_data)
        if bridge.direction is not BridgeDirection.OUTBOUND:
            raise ChannelProcessError("channel process accepts outbound bridge envelopes only")
        if bridge.idempotency_key != str((bridge.payload or {}).get("outbound", {}).get("idempotency_key") or ""):
            raise ChannelProcessError("bridge and outbound idempotency keys do not match")
        if bridge.payload.get("channel_instance_id") != self.manifest.channel_instance_id:
            raise ChannelProcessError("bridge channel instance mismatch")
        outbound_data = bridge.payload.get("outbound")
        if not isinstance(outbound_data, dict):
            raise ChannelProcessError("bridge payload must contain outbound envelope")
        envelope = OutboundEnvelope(
            identity=ChannelIdentity(**outbound_data["identity"]),
            conversation=ChannelConversation(**outbound_data["conversation"]),
            event_type=outbound_data["event_type"],
            payload=outbound_data["payload"],
            idempotency_key=outbound_data["idempotency_key"],
            reply_to_external_id=outbound_data.get("reply_to_external_id"),
            group_id=outbound_data.get("group_id"),
            session_id=outbound_data.get("session_id"),
            source_event_id=outbound_data.get("source_event_id"),
            protocol_version=outbound_data.get("protocol_version", "channel.v1"),
        )
        expected_channel = self.manifest.channel_instance_id.split(":", 1)[0].lower()
        if envelope.identity.channel != expected_channel:
            raise ChannelProcessError("outbound channel does not match process manifest")
        receipt = await asyncio.wait_for(self.handler.send(envelope), timeout=self.manifest.max_seconds)
        if not isinstance(receipt, DeliveryReceipt):
            raise ChannelProcessError("handler returned invalid delivery receipt")
        if receipt.channel != envelope.identity.channel or receipt.idempotency_key != envelope.idempotency_key:
            raise ChannelProcessError("handler receipt does not match request")
        return receipt

    async def _write(self, writer: asyncio.StreamWriter, response: dict) -> None:
        encoded = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > self.manifest.max_frame_bytes:
            raise ChannelProcessError("response frame exceeds limit")
        writer.write(encoded)
        await writer.drain()

    async def _write_error(self, writer: asyncio.StreamWriter, request_id: str, message: str) -> None:
        await self._write(writer, {"request_id": request_id, "error": redact_secrets(message)[0]})


def _safe_error(exc: Exception) -> str:
    return redact_secrets(str(exc)[:2_000])[0]
