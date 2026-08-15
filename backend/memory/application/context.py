"""Host dependency context for Memory application use cases.

The application layer owns no concrete database or runtime singleton.  Hosts
install a database port at the composition boundary; use cases may then use
the ambient context for legacy function-style entry points, while explicit
service constructors remain preferred for embedding.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

from memory.ports import MemoryDatabasePort

_database: ContextVar[MemoryDatabasePort | None] = ContextVar(
    "memory_database", default=None
)
_services: dict[str, ContextVar[Any]] = {
    name: ContextVar(f"memory_{name}", default=None)
    for name in (
        "learning", "pipeline", "skill_compiler", "skill_projection",
        "experience_distiller", "projection_outbox", "projection_reconciler", "model",
    )
}


def configure_database(database: MemoryDatabasePort) -> None:
    _database.set(database)


def configure_service(name: str, service: Any) -> None:
    try:
        _services[name].set(service)
    except KeyError as exc:
        raise ValueError(f"unknown Memory application service: {name}") from exc


def require_service(name: str, fallback: Callable[[], Any]) -> Any:
    try:
        service = _services[name].get()
    except KeyError as exc:
        raise ValueError(f"unknown Memory application service: {name}") from exc
    if service is None:
        service = fallback()
        _services[name].set(service)
    return service


def require_database() -> MemoryDatabasePort:
    database = _database.get()
    if database is None:
        # Transitional host default for function-style entry points.  It is
        # resolved lazily, never imported by application modules at import
        # time, and can be omitted entirely by standalone hosts that inject a
        # port through configure_database().
        from memory.infrastructure.sqlite_database import SQLiteMemoryDatabase
        database = SQLiteMemoryDatabase()
        _database.set(database)
    return database


def require_learning() -> Any:
    return require_service("learning", lambda: _canonical_factory("build_learning_client"))


def require_pipeline() -> Any:
    return require_service("pipeline", lambda: _canonical_factory("build_pipeline_dispatcher"))


def require_skill_compiler() -> Any:
    return require_service("skill_compiler", lambda: _canonical_factory("build_skill_compiler"))


def require_skill_projection() -> Any:
    return require_service("skill_projection", lambda: _canonical_factory("build_skill_projection_client"))


def require_experience_distiller() -> Any:
    return require_service("experience_distiller", lambda: _canonical_factory("build_experience_distiller"))


def require_projection_outbox() -> Any:
    return require_service("projection_outbox", lambda: _canonical_factory("build_projection_outbox"))


def require_projection_reconciler() -> Any:
    return require_service("projection_reconciler", lambda: _canonical_factory("build_projection_reconciler"))


def require_model() -> Any:
    service = _services["model"].get()
    if service is None:
        from memory.canonical import call_memory_model
        service = call_memory_model
        _services["model"].set(service)
    return service


def _canonical_factory(name: str) -> Any:
    # This is the single legacy host bridge.  Standalone hosts should register
    # concrete services with configure_service and never load this fallback.
    from memory import canonical
    factory = getattr(canonical, name)
    return factory()
    return database
