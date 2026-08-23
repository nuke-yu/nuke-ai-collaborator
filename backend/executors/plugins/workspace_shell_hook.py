"""Pre-execution shell safety hook."""


async def shell_guard(name: str, arguments: dict, context: dict, *, check_shell_command) -> dict | None:
    if name != "run_shell":
        return None
    if context.get("ruleset") is None:
        return {"block": True, "reason": "run_shell 未接入权限系统（无 ruleset），出于安全已拒绝执行"}
    command = (arguments.get("cmd") or "").strip()
    blocked, reason = check_shell_command(command)
    if blocked:
        return {"block": True, "reason": f"{reason}（命令：{command}）"}
    return None
