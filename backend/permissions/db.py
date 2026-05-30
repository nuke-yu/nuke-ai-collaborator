"""
DB persistence for permission rules.
Table DDL lives in database.init_db() alongside all other tables.
"""
import os

import db as _db
from .models import Rule

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "chat.db")


async def load_rules(bot_id: int) -> list[Rule]:
    async with _db.connect(_DB_PATH) as db:
        async with db.execute(
            "SELECT id, tool_pattern, args_pattern, action FROM permission_rules WHERE bot_id=?",
            (bot_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [Rule(id=r[0], tool_pattern=r[1], args_pattern=r[2], action=r[3]) for r in rows]


async def save_rule(bot_id: int, tool_pattern: str, args_pattern: str = "", action: str = "allow") -> int:
    async with _db.connect(_DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO permission_rules (bot_id, tool_pattern, args_pattern, action) VALUES (?,?,?,?)",
            (bot_id, tool_pattern, args_pattern, action),
        )
        await db.commit()
        return cur.lastrowid


async def delete_rule(rule_id: int) -> None:
    async with _db.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM permission_rules WHERE id=?", (rule_id,))
        await db.commit()
