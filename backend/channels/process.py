"""Structured JSONL boundary for running a Channel connector out of process."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from executors.redaction import redact_secrets

from .core import (
    BridgeDirection,
    BridgeEnvelope,
    DeliveryReceipt,
    OutboundEnvelope,
    canonical_channel_instance_id,
)
from .secrets import ChannelSecretResolver, EnvironmentSecretResolver, validate_env_names


class ChannelProcessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ChannelProcessManifest:
    channel_instance_id: str
    version: str = "1"
    max_seconds: float = 30.0
    max_frame_bytes: int = 256_000
    env_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.channel_instance_id.strip() or not self.version.strip():
            raise ValueError("channel_instance_id and version are required")
        if self.max_seconds <= 0 or self.max_frame_bytes <= 0:
            raise ValueError("process limits must be positive")
        object.__setattr__(
            self, "channel_instance_id", canonical_channel_instance_id(self.channel_instance_id)
        )
        object.__setattr__(self, "env_keys", validate_env_names(self.env_keys))


class ChannelProcessClient:
    """Send only BridgeEnvelope frames to an isolated Channel process."""

    def __init__(self, argv: list[str], manifest: ChannelProcessManifest, *, secret_resolver: ChannelSecretResolver | None = None):
        if not argv:
            raise ValueError("channel process argv is required")
        self.argv = tuple(argv)
        self.manifest = manifest
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()

    async def send(self, envelope: OutboundEnvelope) -> DeliveryReceipt:
        bridge = BridgeEnvelope(
            direction=BridgeDirection.OUTBOUND,
            event_type=envelope.event_type,
            idempotency_key=envelope.idempotency_key,
            payload={"outbound": envelope.to_dict(), "channel_instance_id": self.manifest.channel_instance_id},
            group_id=envelope.group_id,
        )
        frame = {"bridge": bridge.to_dict(), "manifest": {"channel_instance_id": self.manifest.channel_instance_id, "version": self.manifest.version}}
        async with self._lock:
            self._request_id += 1
            request_id = f"{self.manifest.channel_instance_id}:{self._request_id}"
            frame["request_id"] = request_id
            encoded = (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            if len(encoded) > self.manifest.max_frame_bytes:
                raise ChannelProcessError("channel process frame exceeds limit")
            try:
                await self._start()
                if self._process is None or self._process.stdin is None or self._process.stdout is None:
                    raise ChannelProcessError("channel process is not ready")
                self._process.stdin.write(encoded)
                await self._process.stdin.drain()
                raw = await asyncio.wait_for(self._process.stdout.readline(), timeout=self.manifest.max_seconds)
                if not raw or len(raw) > self.manifest.max_frame_bytes:
                    raise ChannelProcessError("invalid or oversized channel process response")
                response = json.loads(raw.decode())
                if not isinstance(response, dict) or response.get("request_id") != request_id:
                    raise ChannelProcessError("channel process request_id mismatch")
                receipt = response.get("receipt")
                if not isinstance(receipt, dict):
                    raise ChannelProcessError("channel process response must contain receipt")
                if receipt.get("idempotency_key") != envelope.idempotency_key:
                    raise ChannelProcessError("channel process receipt key mismatch")
                safe = json.loads(json.dumps(receipt, ensure_ascii=False))
                for key, value in list(safe.items()):
                    if isinstance(value, str):
                        safe[key] = redact_secrets(value)[0]
                return DeliveryReceipt(**safe)
            except ChannelProcessError:
                await self._stop_process()
                raise
            except asyncio.CancelledError:
                await self._stop_process()
                raise
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                await self._stop_process()
                raise ChannelProcessError("channel process response failed") from exc

    async def _start(self) -> None:
        if self._process is None or self._process.returncode is not None:
            resolved = self._secret_resolver.resolve(self.manifest.channel_instance_id, self.manifest.env_keys)
            env = {"PATH": os.environ.get("PATH", ""), "LANG": os.environ.get("LANG", "C"), "LC_ALL": os.environ.get("LC_ALL", "C"), "PYTHONUNBUFFERED": "1"}
            env.update({str(key): str(value) for key, value in resolved.items() if key in self.manifest.env_keys})
            self._process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=self.manifest.max_frame_bytes,
                env=env,
            )

    async def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=1)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            try:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=1)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                pass

    async def close(self) -> None:
        await self._stop_process()
