"""Temporary host bridge for legacy function-style entry points.

Standalone hosts should never load this module: inject all services through
``memory.application.context.configure_service`` from their composition root.
Keeping this bridge outside application code makes the remaining compatibility
surface explicit and mechanically auditable.
"""
from __future__ import annotations

import importlib
from typing import Any


def canonical_factory(name: str) -> Any:
    from memory import canonical
    return getattr(canonical, name)()


def default_member_directory() -> Any:
    from memory.infrastructure.member_directory import CentralMemberDirectory
    return CentralMemberDirectory()


def default_secret_provider() -> Any:
    from memory.infrastructure.secret_provider import CurrentMemorySecretProvider
    return CurrentMemorySecretProvider()


def default_skill_workspace() -> Any:
    from memory.infrastructure.skill_workspace import CurrentSkillWorkspace
    return CurrentSkillWorkspace()


def default_fact_engine() -> Any:
    module = importlib.import_module("memory.adapters.algorithms")
    return module.Mem0FactEngine()


def default_settings() -> Any:
    from memory.infrastructure.settings import CurrentMemorySettings
    return CurrentMemorySettings()


def default_database() -> Any:
    from memory.infrastructure.sqlite_database import SQLiteMemoryDatabase
    return SQLiteMemoryDatabase()


def default_model() -> Any:
    from memory.canonical import call_memory_model
    return call_memory_model
