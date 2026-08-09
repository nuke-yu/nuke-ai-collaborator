"""Line-delimited JSON IPC boundary for isolated high-risk plugins."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from executors.redaction import redact_secrets


@dataclass(frozen=True, slots=True)
class PluginCapability:
    filesystem_scope: tuple[str, ...] = ()
    network: bool = False
    max_seconds: float = 30.0
    max_output_bytes: int = 256_000


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    version: str
    capability: PluginCapability = field(default_factory=PluginCapability)

    def __post_init__(self) -> None:
        if not self.plugin_id.strip() or not self.version.strip():
            raise ValueError("plugin_id and version are required")
        if self.capability.max_seconds <= 0 or self.capability.max_output_bytes <= 0:
            raise ValueError("plugin resource limits must be positive")


class PluginProcessError(RuntimeError):
    pass


class PluginProcessClient:
    """Own one plugin subprocess and enforce request/response boundaries."""

    def __init__(self, argv: list[str], manifest: PluginManifest):
        if not argv:
            raise ValueError("plugin process argv is required")
        self.argv = tuple(argv)
        self.manifest = manifest
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0

    async def start(self) -> None:
        if self._process is None or self._process.returncode is not None:
            self._process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

    async def call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.start()
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise PluginProcessError("plugin process is not ready")
        self._request_id += 1
        request = {
            "request_id": f"{self.manifest.plugin_id}:{self._request_id}",
            "method": method,
            "payload": payload or {},
            "capability": {
                "filesystem_scope": list(self.manifest.capability.filesystem_scope),
                "network": self.manifest.capability.network,
                "max_output_bytes": self.manifest.capability.max_output_bytes,
            },
        }
        self._process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
        await self._process.stdin.drain()
        try:
            raw = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=self.manifest.capability.max_seconds,
            )
        except asyncio.TimeoutError as exc:
            await self.cancel()
            raise PluginProcessError("plugin request timed out") from exc
        if not raw:
            raise PluginProcessError("plugin process exited without a response")
        if len(raw) > self.manifest.capability.max_output_bytes:
            raise PluginProcessError("plugin response exceeds output limit")
        try:
            response = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginProcessError("invalid plugin response") from exc
        if not isinstance(response, dict):
            raise PluginProcessError("plugin response must be an object")
        safe = json.loads(json.dumps(response, ensure_ascii=False))
        if "result" in safe:
            safe["result"] = redact_secrets(str(safe["result"]))[0]
        return safe

    async def cancel(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

    async def close(self) -> None:
        await self.cancel()
