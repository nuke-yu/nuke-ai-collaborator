"""Workspace-boundary checks for shell command path arguments."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
import logging

log = logging.getLogger(__name__)


def check_shell_command_paths(cmd: str, work_dir: Path, *, path_cls=Path, regex=re, logger=log) -> str | None:
    home = path_cls("~").expanduser().resolve()
    home_text = str(home)
    pattern = r"(?:/Users/|/home/|~)(?:/[a-zA-Z0-9_\-\.]+)+"
    for match in regex.findall(pattern, cmd):
        try:
            if not path_cls(match).expanduser().resolve().is_relative_to(work_dir.resolve()):
                return f"工作区沙箱限制：禁止读写工作区外的路径「{match}」"
        except Exception:
            logger.exception("workspace_tools: failed to validate shell path candidate %s", match)
    if home_text in cmd:
        for word in regex.split(r'[\s\'\"<>\|;&]+', cmd):
            if home_text in word:
                try:
                    if not path_cls(word).expanduser().resolve().is_relative_to(work_dir.resolve()):
                        return f"工作区沙箱限制：禁止读写工作区外的路径「{word}」"
                except Exception:
                    logger.exception("workspace_tools: failed to validate shell home-path candidate %s", word)
    # Relative traversal is not covered by absolute/Home path patterns. Use
    # shell-aware tokenization and resolve traversal candidates against the
    # command working directory before execution.
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = []
    root = path_cls(work_dir).resolve()
    for token in tokens:
        for candidate in (token, token.split("=", 1)[-1]):
            normalized = candidate.replace("\\", "/")
            parts = normalized.split("/")
            if ".." not in parts:
                continue
            try:
                resolved = path_cls(candidate)
                if not resolved.is_absolute():
                    resolved = root / resolved
                if not resolved.expanduser().resolve().is_relative_to(root):
                    return f"工作区沙箱限制：禁止读写工作区外的路径「{candidate}」"
            except Exception:
                logger.exception("workspace_tools: failed to validate relative path candidate %s", candidate)
    return None
