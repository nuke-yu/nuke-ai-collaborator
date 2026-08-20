"""Architecture contracts for reusable runtime feature bounded contexts."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1] / "runtime_features"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result


def test_code_mode_application_is_host_independent() -> None:
    forbidden = ("workspace", "db", "core", "ai", "skills", "executors", "memory")
    for name in ("application.py", "domain.py", "ports.py", "validator.py"):
        path = ROOT / "code_mode" / name
        for imported in _imports(path):
            assert not any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in forbidden
            ), (path, imported)


def test_code_mode_public_package_exposes_composition_entrypoint() -> None:
    from runtime_features.code_mode import CodeModeService, run_code

    assert CodeModeService is not None
    assert callable(run_code)
