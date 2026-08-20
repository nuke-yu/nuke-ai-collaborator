"""Compatibility facade for the Code Mode bounded context.

New code should import ``runtime_features.code_mode``.  This facade remains so
existing tool registration and external plugins can migrate independently.
"""
from __future__ import annotations

from runtime_features.code_mode.application import CodeModeService, append_code_mode_prompt
from runtime_features.code_mode.composition import run_code
from runtime_features.code_mode.domain import (
    CODE_MODE_PROMPT,
    CodeModeLimits,
    CodeModeRejected,
)


__all__ = [
    "CODE_MODE_PROMPT", "CodeModeLimits", "CodeModeRejected",
    "CodeModeService", "append_code_mode_prompt", "run_code",
]
