"""Safe, declarative runtime configuration patches.

``nuke.patch.yml`` changes values, never Python objects.  The schema is
explicit and validated before any target attribute is mutated.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)
MAX_PATCH_BYTES = 64 * 1024


class PatchConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PatchReport:
    path: str
    sha256: str
    applied: tuple[str, ...]


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise PatchConfigError(f"重复配置键: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _string(value: Any, key: str, *, pattern: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PatchConfigError(f"{key} 必须是非空字符串")
    value = value.strip()
    if pattern and not re.fullmatch(pattern, value):
        raise PatchConfigError(f"{key} 包含不允许的值")
    return value


def _bounded_int(value: Any, key: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PatchConfigError(f"{key} 必须是 {low} 到 {high} 的整数")
    return value


def _bounded_float(value: Any, key: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise PatchConfigError(f"{key} 必须是 {low} 到 {high} 的数字")
    return float(value)


def _validate_settings(settings: Any) -> dict[str, Any]:
    if not isinstance(settings, dict):
        raise PatchConfigError("settings 必须是 mapping")
    allowed = {
        "storage_backend", "tool_result_max_chars", "mcp_call_timeout_seconds", "shell_exec_backend",
        "sandbox", "lsp_idle_timeout_s",
    }
    unknown = set(settings) - allowed
    if unknown:
        raise PatchConfigError(f"未知配置键: {sorted(unknown)}")
    updates: dict[str, Any] = {}
    if "storage_backend" in settings:
        backend = _string(
            settings["storage_backend"], "storage_backend",
            pattern=r"[A-Za-z0-9_-]{1,64}",
        )
        updates["__STORAGE_BACKEND__"] = backend
    if "tool_result_max_chars" in settings:
        updates["TOOL_RESULT_MAX_CHARS"] = _bounded_int(
            settings["tool_result_max_chars"], "tool_result_max_chars", 1024, 1_000_000
        )
    if "mcp_call_timeout_seconds" in settings:
        updates["MCP_CALL_TIMEOUT_SECONDS"] = _bounded_float(
            settings["mcp_call_timeout_seconds"], "mcp_call_timeout_seconds", 1.0, 600.0
        )
    if "shell_exec_backend" in settings:
        backend = _string(settings["shell_exec_backend"], "shell_exec_backend")
        if backend not in {"local", "container", "auto"}:
            raise PatchConfigError("shell_exec_backend 只能是 local/container/auto")
        updates["SHELL_EXEC_BACKEND"] = backend
    if "lsp_idle_timeout_s" in settings:
        updates["LSP_IDLE_TIMEOUT_S"] = _bounded_int(
            settings["lsp_idle_timeout_s"], "lsp_idle_timeout_s", 60, 86_400
        )
    if "sandbox" in settings:
        sandbox = settings["sandbox"]
        if not isinstance(sandbox, dict):
            raise PatchConfigError("sandbox 必须是 mapping")
        allowed_sandbox = {"image", "memory", "cpus", "network", "idle_timeout_s"}
        unknown = set(sandbox) - allowed_sandbox
        if unknown:
            raise PatchConfigError(f"未知 sandbox 配置键: {sorted(unknown)}")
        if "image" in sandbox:
            updates["SANDBOX_IMAGE"] = _string(sandbox["image"], "sandbox.image", r"[A-Za-z0-9._:/-]{1,200}")
        if "memory" in sandbox:
            updates["SANDBOX_MEMORY"] = _string(sandbox["memory"], "sandbox.memory", r"[0-9]+[kKmMgG]?")
        if "cpus" in sandbox:
            updates["SANDBOX_CPUS"] = _string(sandbox["cpus"], "sandbox.cpus", r"[0-9]+(?:\.[0-9]+)?")
        if "network" in sandbox:
            network = _string(sandbox["network"], "sandbox.network")
            if network not in {"none", "bridge"}:
                raise PatchConfigError("sandbox.network 只能是 none 或 bridge")
            updates["SANDBOX_NETWORK"] = network
        if "idle_timeout_s" in sandbox:
            updates["SANDBOX_IDLE_TIMEOUT_S"] = _bounded_int(
                sandbox["idle_timeout_s"], "sandbox.idle_timeout_s", 60, 86_400
            )
    return updates


def apply_patch_file(path: str | Path | None = None, *, target: Any | None = None) -> PatchReport | None:
    """Validate and apply a patch file; absent files are a no-op."""
    if path is None:
        path = os.environ.get("NUKE_PATCH_FILE") or (Path(__file__).resolve().parents[2] / "nuke.patch.yml")
    patch_path = Path(path).resolve()
    if not patch_path.exists():
        return None
    raw = patch_path.read_bytes()
    if len(raw) > MAX_PATCH_BYTES:
        raise PatchConfigError("nuke.patch.yml 超过大小限制")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader) or {}
    except UnicodeDecodeError as exc:
        raise PatchConfigError("nuke.patch.yml 必须是 UTF-8") from exc
    except yaml.YAMLError as exc:
        raise PatchConfigError(f"YAML 解析失败: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"version", "settings"}:
        raise PatchConfigError("顶层只允许 version 和 settings")
    if document["version"] != 1:
        raise PatchConfigError("只支持 nuke.patch.yml version: 1")
    updates = _validate_settings(document["settings"])
    if target is None:
        from core import config as target
    storage_backend = updates.pop("__STORAGE_BACKEND__", None)
    if storage_backend is not None:
        from db.adapters import select_storage_backend
        select_storage_backend(storage_backend)
    for attribute, value in updates.items():
        setattr(target, attribute, value)
    applied = list(updates)
    if storage_backend is not None:
        applied.append("storage_backend")
    report = PatchReport(str(patch_path), digest, tuple(sorted(applied)))
    log.info("Applied nuke.patch.yml sha256=%s keys=%s", digest, report.applied)
    return report
