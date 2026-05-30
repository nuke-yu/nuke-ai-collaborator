from fastapi import APIRouter, HTTPException

from db import get_db, get_member
from .db import load_rules, save_rule, delete_rule

router = APIRouter()

_VALID_ACTIONS = {"allow", "deny"}


async def _require_bot_member(member_id: int) -> None:
    """Reject permission ops targeting a missing or non-bot member.

    No auth system exists in this single-machine app, so the boundary check
    is: permission rules only apply to bot members. This stops a caller from
    silently granting rules to arbitrary/nonexistent ids or human members.
    """
    async with get_db() as db:
        member = await get_member(db, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
    if member.get("type") != "bot":
        raise HTTPException(status_code=403, detail="permissions apply to bot members only")


@router.get("/api/members/{member_id}/permissions")
async def get_permission_rules(member_id: int):
    await _require_bot_member(member_id)
    rules = await load_rules(member_id)
    return [
        {"id": r.id, "tool_pattern": r.tool_pattern, "args_pattern": r.args_pattern, "action": r.action}
        for r in rules
    ]


@router.post("/api/members/{member_id}/permissions")
async def add_permission_rule(member_id: int, data: dict):
    await _require_bot_member(member_id)
    tool_pattern = data.get("tool_pattern")
    if not tool_pattern:
        raise HTTPException(status_code=400, detail="tool_pattern is required")
    action = data.get("action", "allow")
    if action not in _VALID_ACTIONS:
        raise HTTPException(status_code=400, detail="action must be 'allow' or 'deny'")
    rule_id = await save_rule(
        member_id,
        tool_pattern,
        data.get("args_pattern", ""),
        action,
    )
    return {"id": rule_id}


@router.delete("/api/members/{member_id}/permissions/{rule_id}")
async def remove_permission_rule(member_id: int, rule_id: int):
    await _require_bot_member(member_id)
    await delete_rule(rule_id)
    return {"ok": True}
