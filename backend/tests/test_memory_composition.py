from __future__ import annotations

from memory.bootstrap import get_memory_composition, get_memory_module


def test_legacy_module_accessor_uses_explicit_composition() -> None:
    composition = get_memory_composition()

    assert composition.module is get_memory_module()
    assert composition.database is composition.module.database
    assert composition.projection_outbox is composition.module.projection_outbox
