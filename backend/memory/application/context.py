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
        "experience_distiller", "projection_outbox", "projection_reconciler", "member_directory", "secret_provider", "skill_workspace", "fact_engine", "settings", "model",
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
        # Implicit host fallbacks are deliberately not cached: test hosts and
        # embedded applications may replace their database/config between
        # calls. Explicit configure_service() remains the stable production
        # path and is cached by the ContextVar.
        return fallback()
    return service


def require_database() -> MemoryDatabasePort:
    database = _database.get()
    if database is None:
        from memory.legacy_fallback import default_database
        return default_database()
    return database


def require_learning() -> Any:
    return require_service("learning", lambda: _legacy_factory("build_learning_client"))


def require_pipeline() -> Any:
    return require_service("pipeline", lambda: _legacy_factory("build_pipeline_dispatcher"))


def require_skill_compiler() -> Any:
    return require_service("skill_compiler", lambda: _legacy_factory("build_skill_compiler"))


def require_skill_projection() -> Any:
    return require_service("skill_projection", lambda: _legacy_factory("build_skill_projection_client"))


def require_experience_distiller() -> Any:
    return require_service("experience_distiller", lambda: _legacy_factory("build_experience_distiller"))


def require_projection_outbox() -> Any:
    return require_service("projection_outbox", lambda: _legacy_factory("build_projection_outbox"))


def require_projection_reconciler() -> Any:
    return require_service("projection_reconciler", lambda: _legacy_factory("build_projection_reconciler"))


def require_member_directory() -> Any:
    return require_service("member_directory", _legacy_default_member_directory)


def require_secret_provider() -> Any:
    return require_service("secret_provider", _legacy_default_secret_provider)


def require_skill_workspace() -> Any:
    return require_service("skill_workspace", _legacy_default_skill_workspace)


def require_fact_engine() -> Any:
    return require_service("fact_engine", _legacy_default_fact_engine)


def require_settings() -> Any:
    return require_service("settings", _legacy_default_settings)


def require_model() -> Any:
    service = _services["model"].get()
    if service is None:
        from memory.legacy_fallback import default_model
        service = default_model()
    return service


def _legacy_factory(name: str) -> Any:
    from memory.legacy_fallback import canonical_factory
    return canonical_factory(name)


def _legacy_default_member_directory() -> Any:
    from memory.legacy_fallback import default_member_directory
    return default_member_directory()


def _legacy_default_secret_provider() -> Any:
    from memory.legacy_fallback import default_secret_provider
    return default_secret_provider()


def _legacy_default_skill_workspace() -> Any:
    from memory.legacy_fallback import default_skill_workspace
    return default_skill_workspace()


def _legacy_default_fact_engine() -> Any:
    from memory.legacy_fallback import default_fact_engine
    return default_fact_engine()


def _legacy_default_settings() -> Any:
    from memory.legacy_fallback import default_settings
    return default_settings()
