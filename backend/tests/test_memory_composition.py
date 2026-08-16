from __future__ import annotations

import asyncio
from dataclasses import replace
import pytest

from memory.application.context import (
    configure_database,
    configure_service,
    configure_standalone_mode,
    require_database,
    require_learning,
    reset_memory_context,
    standalone_mode_enabled,
)
from memory.bootstrap import (
    build_memory_composition,
    memory_context,
    memory_composition,
    memory_module,
)


def test_memory_composition_owns_canonical_dependencies() -> None:
    composition = build_memory_composition()

    assert composition.database is composition.module.database
    assert composition.projection_outbox is composition.module.projection_outbox
    assert "multi_hop_retrieval" in composition.temporal_graph.descriptor.capabilities


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


def test_scoped_composition_restores_previous_bindings():
    sentinel_database = object()
    configure_database(sentinel_database)  # type: ignore[arg-type]
    composition = build_memory_composition()

    async def exercise() -> None:
        async with memory_context(composition):
            assert require_database() is composition.database
            assert memory_composition() is composition

    asyncio.run(exercise())

    assert require_database() is sentinel_database
    reset_memory_context()


def test_scoped_composition_does_not_stop_owner_started_module():
    composition = build_memory_composition()

    async def exercise() -> None:
        await composition.module.start()
        try:
            async with memory_context(composition):
                assert composition.module.running
            assert composition.module.running
        finally:
            await composition.module.stop()

    asyncio.run(exercise())


def test_install_failure_does_not_partially_replace_context():
    sentinel_database = object()
    sentinel_learning = object()
    configure_database(sentinel_database)  # type: ignore[arg-type]
    configure_service("learning", sentinel_learning)
    composition = replace(build_memory_composition(), member_directory=object())

    with pytest.raises(TypeError, match="member_directory"):
        from memory.bootstrap import install_memory_composition
        install_memory_composition(composition)

    assert require_database() is sentinel_database
    assert require_learning() is sentinel_learning
    reset_memory_context()


def test_scoped_composition_restores_standalone_mode():
    configure_standalone_mode(True)
    composition = build_memory_composition()

    async def exercise() -> None:
        async with memory_context(composition):
            assert standalone_mode_enabled() is True

    asyncio.run(exercise())
    assert standalone_mode_enabled() is True
    reset_memory_context()
