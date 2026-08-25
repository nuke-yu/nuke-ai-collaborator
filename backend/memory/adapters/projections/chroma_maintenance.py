"""Safe, offline maintenance primitives for the derived Chroma index."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ChromaCompatibilityError(RuntimeError):
    """The on-disk store is not safe to open with this Chroma runtime."""


# This is intentionally an allow-list, not a guess.  Add a row only after the
# corresponding Chroma release has been tested against a copied production DB.
_SUPPORTED_SYSDB_SCHEMAS: dict[str, frozenset[int]] = {
    "1": frozenset({7}),  # chromadb 1.5.x, pinned in requirements.lock
}


def inspect_store(path: str | Path, runtime_version: str) -> dict[str, Any]:
    """Return a small, dependency-free compatibility report without writing."""
    root = Path(path)
    db_file = root / "chroma.sqlite3"
    report: dict[str, Any] = {
        "path": str(root), "runtime_version": runtime_version,
        "exists": root.exists(), "database_exists": db_file.is_file(),
        "schema_version": None, "compatible": True, "reason": "new store",
    }
    if not root.exists():
        return report
    if not db_file.is_file():
        report.update(compatible=False, reason="missing chroma.sqlite3")
        return report
    try:
        with sqlite3.connect(f"file:{db_file}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT version FROM migrations WHERE dir='sysdb' ORDER BY version"
            ).fetchall()
    except sqlite3.Error as exc:
        report.update(compatible=False, reason=f"unreadable metadata: {type(exc).__name__}")
        return report
    if not rows:
        report.update(compatible=False, reason="missing sysdb migration metadata")
        return report
    report["schema_version"] = max(int(row[0]) for row in rows)
    major = runtime_version.split(".", 1)[0]
    supported = _SUPPORTED_SYSDB_SCHEMAS.get(major, frozenset())
    if report["schema_version"] not in supported:
        report.update(
            compatible=False,
            reason=(f"sysdb schema {report['schema_version']} is not validated for "
                    f"Chroma major {major} (supported: {sorted(supported)})"),
        )
    else:
        report["reason"] = "validated runtime/schema pair"
    return report


def require_compatible_store(path: str | Path, runtime_version: str, expected_version: str | None = None) -> dict[str, Any]:
    report = inspect_store(path, runtime_version)
    if expected_version and runtime_version != expected_version:
        raise ChromaCompatibilityError(
            f"installed Chroma version {runtime_version!r} does not match requested {expected_version!r}"
        )
    if not report["compatible"]:
        raise ChromaCompatibilityError(
            f"Chroma store at {report['path']!r} is unsafe to open: {report['reason']}. "
            "Restore a backup or use the rebuild command."
        )
    return report


def backup_store(path: str | Path, backup_root: str | Path | None = None) -> Path:
    """Copy the complete persistent index before any migration writes."""
    source = Path(path)
    if not source.is_dir():
        raise FileNotFoundError(f"Chroma directory does not exist: {source}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_base = Path(backup_root) if backup_root else source.parent
    destination_base.mkdir(parents=True, exist_ok=True)
    destination = destination_base / f"{source.name}.backup-{stamp}"
    suffix = 1
    while destination.exists():
        suffix += 1
        destination = destination_base / f"{source.name}.backup-{stamp}-{suffix}"
    shutil.copytree(source, destination)
    return destination


def quarantine_store(path: str | Path) -> Path:
    """Atomically move an unreadable store aside; never delete its evidence."""
    source = Path(path)
    if not source.is_dir():
        raise FileNotFoundError(f"Chroma directory does not exist: {source}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = source.parent / f"{source.name}.pre-rebuild-{stamp}"
    suffix = 1
    while destination.exists():
        suffix += 1
        destination = source.parent / f"{source.name}.pre-rebuild-{stamp}-{suffix}"
    shutil.move(str(source), str(destination))
    return destination
