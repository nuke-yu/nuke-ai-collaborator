from fastapi import APIRouter
from .db import load_rules, save_rule, delete_rule

router = APIRouter()


@router.get("/api/members/{member_id}/permissions")
async def get_permission_rules(member_id: int):
    rules = await load_rules(member_id)
    return [
        {"id": r.id, "tool_pattern": r.tool_pattern, "args_pattern": r.args_pattern, "action": r.action}
        for r in rules
    ]


@router.post("/api/members/{member_id}/permissions")
async def add_permission_rule(member_id: int, data: dict):
    rule_id = await save_rule(
        member_id,
        data["tool_pattern"],
        data.get("args_pattern", ""),
        data.get("action", "allow"),
    )
    return {"id": rule_id}


@router.delete("/api/members/{member_id}/permissions/{rule_id}")
async def remove_permission_rule(member_id: int, rule_id: int):
    await delete_rule(rule_id)
    return {"ok": True}
