"""Deterministic, bounded workspace directory traversal."""
from __future__ import annotations

import os
from pathlib import Path


def walk_visible(root: Path, max_entries: int, skip_hidden: bool, ignored: set[str]) -> tuple[list[Path], bool]:
    if not root.exists():
        return [], False
    paths: list[Path] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        all_dirs = sorted(dirnames)
        dirnames[:] = [d for d in all_dirs if d not in ignored and (not skip_hidden or not d.startswith("."))]
        base = Path(dirpath)
        for name in all_dirs:
            if skip_hidden:
                if name.startswith(".") or name in ignored:
                    continue
            elif name in ignored and name != ".git":
                continue
            paths.append(base / name)
        for name in sorted(filenames):
            if skip_hidden and name.startswith("."):
                continue
            paths.append(base / name)
        if len(paths) >= max_entries:
            truncated = True
            break
    return sorted(paths)[:max_entries], truncated
