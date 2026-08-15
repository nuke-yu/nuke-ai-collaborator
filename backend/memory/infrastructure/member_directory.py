"""Current host adapter for central member metadata."""
from __future__ import annotations

from typing import Any, Mapping


class CentralMemberDirectory:
    async def get_member(self, member_id: int) -> Mapping[str, Any] | None:
        from db import get_member, global_db

        async with global_db() as database:
            return await get_member(database, member_id)
