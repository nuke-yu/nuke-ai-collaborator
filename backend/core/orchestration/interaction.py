import asyncio
import logging
from bus.events import Read, Message
from db import get_messages
from executors.base import InteractionAdapter
from bus import bus
from db import write_connect, save_message
import sessions

log = logging.getLogger(__name__)

class StandardInteraction(InteractionAdapter):
    """Production implementation of side-effects using the real DB and Bus."""
    def __init__(self, ctx=None):
        self.ctx = ctx

    async def create_session(self, **kwargs):
        await sessions.create_session(**kwargs)

    async def update_session_status(self, session_id: str, status: str):
        await sessions.update_session_status(session_id, status)
        if self.ctx and getattr(self.ctx, "group_id", None) is not None:
            await self.broadcast(self.ctx.group_id, {
                "type": "session_updated",
                "session_id": session_id,
                "status": status,
            })
    
    async def broadcast(self, group_id: int, payload: dict):
        await bus.broadcast(group_id, payload)

    async def save_message(self, group_id: int, member_id: int, content: str, **kwargs) -> int:
        async with write_connect() as db:
            return await save_message(db, group_id, member_id, content, **kwargs)

    async def append_session_event(self, session_id: str, event_type: str, payload: dict):
        await sessions.append_event(session_id, event_type, payload)
        if self.ctx and getattr(self.ctx, "group_id", None) is not None:
            await self.broadcast(self.ctx.group_id, {
                "type": "session_updated",
                "session_id": session_id,
                "event_type": event_type,
            })

    async def save_session_snapshot(self, session_id: str, messages: list):
        await sessions.save_snapshot(session_id, messages)
        if self.ctx and getattr(self.ctx, "group_id", None) is not None:
            await self.broadcast(self.ctx.group_id, {
                "type": "session_updated",
                "session_id": session_id,
            })

    async def update_session_tokens(self, session_id: str, **usage):
        # Translate keys if necessary
        await sessions.add_tokens(
            session_id,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_tokens", 0)
        )
        
        # Point 4: Hang the cost on the current Jira Ticket if applicable
        ticket_id = usage.get("ticket_id") or (getattr(self.ctx, "active_ticket_id", None) if self.ctx else None)
        cost_usd = usage.get("cost_usd")
        
        if ticket_id and cost_usd:
            try:
                async with write_connect() as db:
                    await db.execute(
                        "UPDATE tickets SET total_usd_cost = total_usd_cost + ? WHERE ticket_id = ?",
                        (cost_usd, ticket_id)
                    )
                    await db.commit()
            except Exception:
                log.exception("Failed to update ticket cost")

    async def mark_read(self, group_id: int, member_id: int, msg_id: int):



        async with write_connect() as db:
            await db.execute(
                "INSERT INTO member_read (member_id, group_id, last_read_id) VALUES (?,?,?) "
                "ON CONFLICT(member_id, group_id) DO UPDATE SET last_read_id=excluded.last_read_id",
                (member_id, group_id, msg_id)
            )
            await db.commit()
        await bus.publish(Read(group_id=group_id, member_id=member_id, last_read_id=msg_id))

    async def send_auto_reply(self, group_id: int, member: dict, reply_to_id: int):



        await asyncio.sleep(1.5)

        async with write_connect() as db:
            msg_id = await self.save_message(group_id, member["id"], member["auto_reply"],
                                        reply_to_id=reply_to_id, is_auto_reply=True)
    
            recent = await get_messages(db, group_id)
            saved = next((m for m in recent if m["id"] == msg_id), {})
        await bus.publish(Message(group_id=group_id, **{k: v for k, v in saved.items() if k != "group_id"}))
