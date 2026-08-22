"""Architecture tests for the standalone Memory bounded context."""
from __future__ import annotations

import ast
from pathlib import Path


MEMORY_ROOT = Path(__file__).parents[1] / "memory"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.append(node.module)
    return values


def test_application_does_not_import_host_or_concrete_infrastructure() -> None:
    forbidden = (
        "db", "workspace", "core", "config", "ai", "skills", "executors",
        "channels", "plugins", "runtime", "memory.canonical", "memory.adapters",
        "memory.infrastructure",
    )
    for path in (MEMORY_ROOT / "application").glob("*.py"):
        for imported in _imports(path):
            assert not any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in forbidden
            ), (path, imported)


def test_adapters_do_not_import_application_modules() -> None:
    for path in (MEMORY_ROOT / "adapters").rglob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith("memory.application"), (path, imported)


def test_memory_public_package_imports_without_host_bootstrap() -> None:
    import memory

    assert hasattr(memory, "MemoryModule")
