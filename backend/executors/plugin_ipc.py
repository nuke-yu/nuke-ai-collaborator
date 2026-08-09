"""Line-delimited JSON IPC boundary for isolated high-risk plugins."""
from __future__ import annotations

import asyncio
import hashlib
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
    max_input_bytes: int = 256_000
    allowed_methods: tuple[str, ...] = ()
    write_methods: tuple[str, ...] = ()


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
        if self.capability.max_input_bytes <= 0:
            raise ValueError("plugin input limit must be positive")

    @property
    def canonical_hash(self) -> str:
        canonical = json.dumps({
            "plugin_id": self.plugin_id,
            "version": self.version,
            "capability": {
                "filesystem_scope": list(self.capability.filesystem_scope),
                "network": self.capability.network,
                "max_seconds": self.capability.max_seconds,
                "max_output_bytes": self.capability.max_output_bytes,
                "max_input_bytes": self.capability.max_input_bytes,
                "allowed_methods": list(self.capability.allowed_methods),
                "write_methods": list(self.capability.write_methods),
            },
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


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
        self._call_lock = asyncio.Lock()
        self._state = "stopped"
        self._last_error = ""
        self._restart_count = 0

    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict[str, Any]:
        return {
            "plugin_id": self.manifest.plugin_id,
            "manifest_hash": self.manifest.canonical_hash,
            "state": self._state,
            "restart_count": self._restart_count,
            "last_error": self._last_error,
        }

    async def start(self) -> None:
        if self._process is None or self._process.returncode is not None:
            was_crash = self._process is not None and self._process.returncode is not None and self._state != "stopped"
            self._process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._state = "running"
            self._last_error = ""
            if was_crash:
                self._restart_count += 1

    async def call(self, method: str, payload: dict[str, Any] | None = None, *, human_approved: bool = False) -> dict[str, Any]:
        if not method.strip():
            raise PluginProcessError("plugin method is required")
        capability = self.manifest.capability
        if capability.allowed_methods and method not in capability.allowed_methods:
            raise PluginProcessError(f"plugin method is not allowed: {method}")
        if method in capability.write_methods and not human_approved:
            raise PluginProcessError(f"plugin method requires human approval: {method}")
        encoded_payload = json.dumps(payload or {}, ensure_ascii=False).encode()
        if len(encoded_payload) > capability.max_input_bytes:
            raise PluginProcessError("plugin request exceeds input limit")

        async with self._call_lock:
            await self.start()
            if self._process is None or self._process.stdin is None or self._process.stdout is None:
                raise PluginProcessError("plugin process is not ready")
            self._request_id += 1
            request_id = f"{self.manifest.plugin_id}:{self._request_id}"
            request = {
                "request_id": request_id,
                "method": method,
                "payload": payload or {},
                "manifest": {"plugin_id": self.manifest.plugin_id, "version": self.manifest.version, "hash": self.manifest.canonical_hash},
                "capability": {
                    "filesystem_scope": list(capability.filesystem_scope),
                    "network": capability.network,
                    "max_seconds": capability.max_seconds,
                    "max_output_bytes": capability.max_output_bytes,
                    "max_input_bytes": capability.max_input_bytes,
                },
            }
            try:
                self._process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
                await self._process.stdin.drain()
                raw = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=capability.max_seconds,
                )
                if not raw:
                    raise PluginProcessError("plugin process exited without a response")
                if len(raw) > capability.max_output_bytes:
                    raise PluginProcessError("plugin response exceeds output limit")
                try:
                    response = json.loads(raw.decode())
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise PluginProcessError("invalid plugin response") from exc
                if not isinstance(response, dict):
                    raise PluginProcessError("plugin response must be an object")
                if response.get("request_id") not in (None, request_id):
                    raise PluginProcessError("plugin response request_id mismatch")
                if response.get("manifest_hash") not in (None, self.manifest.canonical_hash):
                    raise PluginProcessError("plugin response manifest mismatch")
                safe = json.loads(json.dumps(response, ensure_ascii=False))
                if "result" in safe:
                    safe["result"] = redact_secrets(str(safe["result"]))[0]
                self._state = "running"
                return safe
            except asyncio.TimeoutError as exc:
                self._last_error = "plugin request timed out"
                await self.cancel()
                raise PluginProcessError(self._last_error) from exc
            except Exception as exc:
                self._last_error = str(exc)
                if self._process is not None and self._process.returncode is not None:
                    self._state = "crashed"
                raise

    async def cancel(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        self._state = "stopped"

    async def close(self) -> None:
        await self.cancel()
