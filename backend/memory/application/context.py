"""Host dependency context for Memory application use cases.

The application layer owns no concrete database or runtime singleton.  Hosts
install a database port at the composition boundary; use cases may then use
the ambient context for legacy function-style entry points, while explicit
service constructors remain preferred for embedding.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

from memory.ports import MemoryDatabasePort, PipelineJobRepositoryPort

_database: ContextVar[MemoryDatabasePort | None] = ContextVar(
    "memory_database", default=None
)
_standalone_strict: ContextVar[bool] = ContextVar(
    "memory_standalone_strict", default=False
)
_composition: ContextVar[Any | None] = ContextVar(
    "memory_composition", default=None
)
_services: dict[str, ContextVar[Any]] = {
    name: ContextVar(f"memory_{name}", default=None)
    for name in (
        "learning", "pipeline", "pipeline_repository", "skill_compiler", "skill_projection",
        "experience_distiller", "projection_outbox", "projection_reconciler", "member_directory", "secret_provider", "skill_workspace", "fact_engine", "settings", "model",
        "temporal_graph",
        "memory_functions",
    )
}


def configure_database(database: MemoryDatabasePort) -> None:
    _database.set(database)


def configure_standalone_mode(enabled: bool = True) -> None:
    """Require explicit host dependencies instead of the project bridge.

    Embedded/standalone hosts should enable this before serving requests.  The
    default remains permissive only for the legacy application entry points in
    the current product; it is not used by the Memory composition itself.
    """
    _standalone_strict.set(bool(enabled))


def standalone_mode_enabled() -> bool:
    return _standalone_strict.get()


def reset_memory_context() -> None:
    """Clear all ambient Memory dependencies in the current task context."""
    _database.set(None)
    _standalone_strict.set(False)
    _composition.set(None)
    for service in _services.values():
        service.set(None)


def capture_memory_context() -> tuple[MemoryDatabasePort | None, bool, Any | None, dict[str, Any]]:
    return (
        _database.get(),
        _standalone_strict.get(),
        _composition.get(),
        {name: variable.get() for name, variable in _services.items()},
    )


def restore_memory_context(
    state: tuple[MemoryDatabasePort | None, bool, Any | None, dict[str, Any]],
) -> None:
    database, standalone, composition, services = state
    _database.set(database)
    _standalone_strict.set(standalone)
    _composition.set(composition)
    for name, variable in _services.items():
        variable.set(services[name])


def configure_composition(composition: Any | None) -> None:
    _composition.set(composition)


def current_composition() -> Any | None:
    return _composition.get()


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
        if _standalone_strict.get():
            raise RuntimeError(
                f"Memory service {name!r} is not configured for standalone host"
            )
        # Implicit host fallbacks are deliberately not cached: test hosts and
        # embedded applications may replace their database/config between
        # calls. Explicit configure_service() remains the stable production
        # path and is cached by the ContextVar.
        return fallback()
    return service


def require_database() -> MemoryDatabasePort:
    database = _database.get()
    if database is None:
        if _standalone_strict.get():
            raise RuntimeError("Memory database is not configured for standalone host")
        from memory.legacy_fallback import default_database
        return default_database()
    return database


def require_temporal_graph() -> Any:
    """Return the composition-owned Graphiti temporal graph adapter."""
    from memory.ports import TemporalGraphPort
    service = require_service("temporal_graph", _legacy_temporal_graph)
    if not isinstance(service, TemporalGraphPort):
        raise TypeError("configured temporal_graph does not implement TemporalGraphPort")
    return service


def require_memory_functions() -> Any:
    """Return the explicitly composed active-memory controller.

    Active memory functions intentionally have no legacy fallback: silently
    constructing one would bypass the composition root's ACL and outbox.
    """
    service = _services["memory_functions"].get()
    if service is None:
        raise RuntimeError("active Memory functions are not configured")
    return service


def _legacy_temporal_graph() -> Any:
    from memory.legacy_fallback import default_temporal_graph
    return default_temporal_graph()


def require_learning() -> Any:
    return require_service("learning", lambda: _legacy_factory("build_learning_client"))


def require_pipeline() -> Any:
    return require_service("pipeline", lambda: _legacy_factory("build_pipeline_dispatcher"))


def require_pipeline_repository(
    database: MemoryDatabasePort | None = None,
) -> PipelineJobRepositoryPort:
    configured = _services["pipeline_repository"].get()
    if configured is None and database is not None:
        from memory.application.pipeline import CanonicalPipelineJobRepository
        repository = CanonicalPipelineJobRepository(database)
    else:
        repository = require_service(
            "pipeline_repository", _legacy_default_pipeline_repository
        )
    if not isinstance(repository, PipelineJobRepositoryPort):
        raise TypeError("configured pipeline_repository does not implement PipelineJobRepositoryPort")
    return repository


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
        if _standalone_strict.get():
            raise RuntimeError("Memory model is not configured for standalone host")
        from memory.legacy_fallback import default_model
        service = default_model()
    return service


def _legacy_factory(name: str) -> Any:
    from memory.legacy_fallback import canonical_factory
    return canonical_factory(name)


def _legacy_default_member_directory() -> Any:
    from memory.legacy_fallback import default_member_directory
    return default_member_directory()


def _legacy_default_pipeline_repository() -> PipelineJobRepositoryPort:
    from memory.application.pipeline import CanonicalPipelineJobRepository
    return CanonicalPipelineJobRepository()


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
