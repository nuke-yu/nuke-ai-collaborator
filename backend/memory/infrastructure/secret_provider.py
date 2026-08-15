"""Current host secret adapter for Memory cursor signing."""
from __future__ import annotations


class CurrentMemorySecretProvider:
    def export_cursor_secret(self) -> bytes:
        from core.auth import SECRET_KEY

        return SECRET_KEY.encode("utf-8")
