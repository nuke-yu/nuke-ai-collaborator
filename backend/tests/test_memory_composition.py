from __future__ import annotations

from memory.bootstrap import build_memory_composition, memory_composition, memory_module


def test_memory_composition_owns_canonical_dependencies() -> None:
    composition = build_memory_composition()

    assert composition.database is composition.module.database
    assert composition.projection_outbox is composition.module.projection_outbox


def test_process_local_accessors_are_canonical():
    assert memory_composition().module is memory_module()
