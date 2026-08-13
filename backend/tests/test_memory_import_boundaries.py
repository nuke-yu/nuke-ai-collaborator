"""Migration guard: the canonical Memory layers must not regress to legacy AI code."""
from __future__ import annotations

import ast
from pathlib import Path


LEGACY_ROOTS = (
    "ai.memory",
    "ai.personal_vault",
    "ai.experiences",
    "ai.skill_learning",
    "ai.pipeline",
    "ai.cases",
    "ai.usage_tracking",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result


def test_canonical_memory_layers_do_not_import_legacy_business_modules() -> None:
    root = Path(__file__).resolve().parents[1] / "memory"
    forbidden = []
    for layer in ("domain", "application", "contracts", "infrastructure", "ports"):
        for path in (root / layer).rglob("*.py"):
            for module in _imports(path):
                if any(module == legacy or module.startswith(legacy + ".") for legacy in LEGACY_ROOTS):
                    forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []


def test_memory_layers_do_not_import_ai_runtime_modules() -> None:
    root = Path(__file__).resolve().parents[1] / "memory"
    allowed = {"bootstrap.py"}
    violations = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if (
            relative.parts[:2] in (("adapters", "runtime"), ("adapters", "projections"))
            or relative.as_posix() in allowed
        ):
            continue
        for module in _imports(path):
            if any(module == legacy or module.startswith(legacy + ".") for legacy in LEGACY_ROOTS):
                violations.append(f"{relative} -> {module}")
    assert violations == []
