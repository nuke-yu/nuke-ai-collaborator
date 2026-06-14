import sys
from executors.plugins.workspace_tools import _IS_WINDOWS, _with_personality, _build_skills_xml
from skills import list_skills_all, load_always_skills, filter_skills_by_context
from skills.traits import load_traits
from executors.base import build_group_section

async def compile_system_prompt(
    bot: dict,
    ctx,
    model_name: str,
    memory: str,
) -> tuple[str, str, list, list]:
    """
    Compiles the system prompt base, loads always-active skills, and runs skill discovery.
    
    Returns:
        system_prompt_base: Compiled base system prompt
        skills_xml: Formatted skills XML text for context injection
        skills_snapshot: Loaded skills snapshot metadata for logging/WS
        always_skills: Loaded always-active skills content
    """
    base = _with_personality(
        bot["system_prompt"] or f"你是{bot['name']}，{bot.get('role', '')}。", bot
    )
    
    skills_xml = ""
    skills_snapshot = []
    always_skills = []
    
    # Skill Discovery
    raw_skills = await list_skills_all(bot["id"], group_id=ctx.group_id, role=bot.get("role"))
    lazy_candidates = [
        s for s in raw_skills
        if not s.get("always")
        and s.get("status", "active") != "disabled"
        and s.get("user_invocable", True)
    ]
    
    # B1: Retrieve current workflow stage and awaiting_confirm state
    import core.workflow as wf
    orch = wf._orch_for(ctx.group_id)
    
    current_stage = orch.current_stage_role(ctx.group_id) if orch else None
    is_awaiting_confirm = orch.is_awaiting_confirm(ctx.group_id) if orch else False

    lazy_candidates = filter_skills_by_context(
        lazy_candidates,
        ctx.user_message,
        bot_role=bot.get("role"),
        current_stage=current_stage,
        is_awaiting_confirm=is_awaiting_confirm
    )
    skills_xml, injected_names = _build_skills_xml(lazy_candidates, model_name)
    
    for s in raw_skills:
        if s.get("status", "active") == "disabled":
            inj = None
        elif s.get("always"):
            inj = "full"
        elif not s.get("user_invocable", True):
            inj = None
        else:
            inj = "metadata" if s["name"] in injected_names else None
        # Ensure all values are JSON-serializable (Path → str) before broadcasting.
        safe = {k: str(v) if hasattr(v, "__fspath__") else v for k, v in s.items()}
        skills_snapshot.append({**safe, "injected": inj})
            
    if any(s.get("always") for s in raw_skills if s.get("status", "active") != "disabled"):
        always_skills = await load_always_skills(bot["id"], ctx.group_id, bot.get("role"))
        
    always_section = ""
    if always_skills:
        parts = [f"=== {s['name']} ===\n{s['content']}" for s in always_skills]
        always_section = "\n\n【常驻技能 · 始终激活】\n" + "\n\n".join(parts)

    group_section = build_group_section(ctx)
    bot_traits = bot.get("traits", [])
    traits_section = load_traits(bot_traits)
    
    os_info = "Windows (PowerShell)" if _IS_WINDOWS else f"{sys.platform} (shell: /bin/sh)"
    
    system_prompt_base = (
        base
        + (f"\n\n{memory}" if memory else "")
        + traits_section
        + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
        + always_section
        + f"\n\n【运行环境】\nOS: {os_info}\n路径分隔符: {'\\' if _IS_WINDOWS else '/'}\n使用 run_shell 执行命令时请使用适合当前 OS 的语法。"
        + "\n\n【自学技能规则】\n当你发现可复用规律或用户说「记住这个做法」时，用 write_file 将技能写入 `skills/learned/draft/<skill-name>.md`，系统会自动请求用户审批。禁止直接写入 `skills/learned/active/`。"
        + ctx.workflow_suffix
    )
    
    return system_prompt_base, skills_xml, skills_snapshot, always_skills


def restrict_schemas(schemas: list, allowed: list | None) -> list:
    """Keep only tool schemas whose name is in `allowed`. `None`/empty = no restriction."""
    if not allowed:
        return schemas
    allowed_set = set(allowed)
    return [s for s in schemas if s["function"]["name"] in allowed_set]


def filter_mcp_schemas(mcp_schemas: list, allow: list | None, block: list | None) -> list:
    """Per-bot MCP tool visibility whitelist/blacklist fnmatch filtering."""
    if not allow and not block:
        return mcp_schemas
    import fnmatch
    out = []
    for s in mcp_schemas:
        name = s.get("function", {}).get("name", "")
        if block and any(fnmatch.fnmatch(name, p) for p in block):
            continue
        if allow and not any(fnmatch.fnmatch(name, p) for p in allow):
            continue
        out.append(s)
    return out


def apply_external_schema_budget(
    mcp_schemas: list, max_n: int = 48
) -> tuple[list, list[str]]:
    """Keep at most max_n external schemas; return (kept, deferred_tool_names)."""
    if len(mcp_schemas) <= max_n:
        return mcp_schemas, []
    kept = mcp_schemas[:max_n]
    deferred = [s["function"]["name"] for s in mcp_schemas[max_n:]]
    return kept, deferred


def build_budget_note(deferred_names: list[str]) -> str:
    shown = ", ".join(deferred_names[:30]) + ("…" if len(deferred_names) > 30 else "")
    return (
        f"\n\n[工具预算] 另有 {len(deferred_names)} 个 MCP 工具因数量预算未加载"
        f"（{shown}）。如需使用，请在 mcp_servers.json 用 allow_list 收窄该服务器，"
        f"或告知用户调整配置。"
    )


async def get_fresh_context_prefix(
    bot_id: int,
    group_id: int | None,
    startup_files: list,
    skills_xml: str,
) -> tuple[str, str]:
    from workspace import load_context_files, format_context_blocks
    blocks = await load_context_files(bot_id, group_id, startup_files)
    text = format_context_blocks(blocks)
    prefix = ""
    if text:
        prefix += f"【工作区文件】\n{text}\n\n"
    if skills_xml:
        prefix += f"{skills_xml}\n使用 run_skill(name=\"技能名\") 调用\n\n"
    return prefix, text


async def build_reinject_context(
    file_tracker: dict,
    bot_id: int,
    group_id: int | None,
    startup_files: list,
    skills_xml: str,
) -> str:
    from skills.constants import bot_ws as _bot_ws
    import executors.compact as compact
    fresh_prefix, _ = await get_fresh_context_prefix(
        bot_id, group_id, startup_files, skills_xml
    )
    ft_xml = compact.build_file_tracker_xml(file_tracker)
    file_contents = compact.build_file_contents_for_reinject(
        file_tracker, workspace_dir=str(_bot_ws(bot_id, group_id))
    )
    parts = [p for p in [fresh_prefix, ft_xml, file_contents] if p]
    return "\n\n".join(parts)

