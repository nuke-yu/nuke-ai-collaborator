"""Current project settings/error adapter."""
from __future__ import annotations

from typing import Any


class CurrentMemorySettings:
    def get(self, name: str, default: Any = None) -> Any:
        from core import config
        return getattr(config, name, default)

    def is_missing_schema_error(self, error: BaseException) -> bool:
        from db.errors import is_missing_schema_error
        return bool(is_missing_schema_error(error))
