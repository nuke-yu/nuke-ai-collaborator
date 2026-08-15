"""Temporary host bridge for legacy function-style entry points.

Standalone hosts should never load this module: inject all services through
``memory.application.context.configure_service`` from their composition root.
Keeping this bridge outside application code makes the remaining compatibility
surface explicit and mechanically auditable.
"""
from __future__ import annotations

import importlib
import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)
_usage: Counter[str] = Counter()


def legacy_fallback_usage() -> dict[str, int]:
    """Return process-local usage counts for the transitional bridge."""
    return dict(_usage)


def reset_legacy_fallback_usage() -> None:
    _usage.clear()


def _used(name: str) -> None:
    _usage[name] += 1
    logger.debug("legacy Memory fallback invoked: %s (count=%d)", name, _usage[name])


def canonical_factory(name: str) -> Any:
    _used(f"canonical:{name}")
    from memory import canonical
    return getattr(canonical, name)()


def default_member_directory() -> Any:
    _used("member_directory")
    from memory.infrastructure.member_directory import CentralMemberDirectory
    return CentralMemberDirectory()


def default_secret_provider() -> Any:
    _used("secret_provider")
    from memory.infrastructure.secret_provider import CurrentMemorySecretProvider
    return CurrentMemorySecretProvider()


def default_skill_workspace() -> Any:
    _used("skill_workspace")
    from memory.infrastructure.skill_workspace import CurrentSkillWorkspace
    return CurrentSkillWorkspace()


def default_fact_engine() -> Any:
    _used("fact_engine")
    module = importlib.import_module("memory.adapters.algorithms")
    return module.Mem0FactEngine()


def default_settings() -> Any:
    _used("settings")
    from memory.infrastructure.settings import CurrentMemorySettings
    return CurrentMemorySettings()


def default_database() -> Any:
    _used("database")
    from memory.infrastructure.sqlite_database import SQLiteMemoryDatabase
    return SQLiteMemoryDatabase()


def default_model() -> Any:
    _used("model")
    from memory.canonical import call_memory_model
    return call_memory_model
