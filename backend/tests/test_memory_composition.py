from __future__ import annotations

import pytest

from memory.application.context import (
    configure_database,
    configure_service,
    configure_standalone_mode,
    require_database,
    require_learning,
    reset_memory_context,
)
from memory.bootstrap import (
    build_memory_composition,
    memory_composition,
    memory_module,
)


def test_memory_composition_owns_canonical_dependencies() -> None:
    composition = build_memory_composition()

    assert composition.database is composition.module.database
    assert composition.projection_outbox is composition.module.projection_outbox


def test_process_local_accessors_are_canonical():
    assert memory_composition().module is memory_module()


def test_build_does_not_mutate_ambient_context():
    sentinel_database = object()
    sentinel_learning = object()
    configure_database(sentinel_database)  # type: ignore[arg-type]
    configure_service("learning", sentinel_learning)

    build_memory_composition()

    assert require_database() is sentinel_database
    assert require_learning() is sentinel_learning
    reset_memory_context()


def test_standalone_context_fails_fast_without_explicit_ports():
    reset_memory_context()
    configure_standalone_mode()

    try:
        with pytest.raises(RuntimeError, match="standalone host"):
            require_database()
        with pytest.raises(RuntimeError, match="standalone host"):
            require_learning()
    finally:
        reset_memory_context()
