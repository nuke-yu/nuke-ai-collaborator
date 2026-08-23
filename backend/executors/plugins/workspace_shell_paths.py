"""Workspace-boundary checks for shell command path arguments."""
from __future__ import annotations

import re
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
    return None
