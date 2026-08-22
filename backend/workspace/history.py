"""Versioned file-history primitives for workspace files."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging

log = logging.getLogger(__name__)


def history_dir(workspace: Path, path: Path) -> Path:
    rel = path.relative_to(workspace)
    parent = str(rel.parent)
    return workspace / ".history" / (rel.stem if parent == "." else f"{parent}/{rel.stem}")


def save_to_history(workspace: Path, path: Path, limit: int = 10) -> None:
    try:
        existing = path.read_text(encoding="utf-8")
        target = history_dir(workspace, path)
        target.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        (target / f"{timestamp}.md").write_text(existing, encoding="utf-8")
        versions = sorted(target.glob("*.md"), key=lambda item: item.name)
        while len(versions) > limit:
            versions.pop(0).unlink(missing_ok=True)
    except Exception:
        log.exception("vfs: failed to save history for %s", path)
