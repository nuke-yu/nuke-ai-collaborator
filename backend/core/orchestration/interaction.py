import logging
from executors.base import InteractionAdapter
from bus import bus
from db import get_db, save_message
import sessions

log = logging.getLogger(__name__)

class StandardInteraction(InteractionAdapter):
    """Production implementation of side-effects using the real DB and Bus."""

    async def create_session(self, **kwargs):
        await sessions.create_session(**kwargs)

    async def update_session_status(self, session_id: str, status: str):
        await sessions.update_session_status(session_id, status)
    
    async def broadcast(self, group_id: int, payload: dict):
        await bus.broadcast(group_id, payload)

    async def save_message(self, group_id: int, member_id: int, content: str, **kwargs) -> int:
        async with get_db() as db:
            return await save_message(db, group_id, member_id, content, **kwargs)

    async def append_session_event(self, session_id: str, event_type: str, payload: dict):
        await sessions.append_event(session_id, event_type, payload)

    async def save_session_snapshot(self, session_id: str, messages: list):
        await sessions.save_snapshot(session_id, messages)

    async def update_session_tokens(self, session_id: str, **usage):
        # Translate keys if necessary
        await sessions.add_tokens(
            session_id,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_tokens", 0)
        )
