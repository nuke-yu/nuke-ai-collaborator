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
    from core.orchestration import registry as orch_registry
    orch = orch_registry.get("workflow_v1")
    s_state = orch.get(ctx.group_id) if (orch and ctx.group_id) else None
    
    current_stage = None
    is_awaiting_confirm = False
    
    if s_state:
        current_idx = s_state.get("current", 0)
        stages = s_state.get("stages", [])
        if 0 <= current_idx < len(stages):
            current_stage = stages[current_idx].get("name")
        if s_state.get("awaiting_confirm"):
            is_awaiting_confirm = True

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
            skills_snapshot.append({**s, "injected": None})
        elif s.get("always"):
            skills_snapshot.append({**s, "injected": "full"})
        elif not s.get("user_invocable", True):
            skills_snapshot.append({**s, "injected": None})
        else:
            inj = "metadata" if s["name"] in injected_names else None
            skills_snapshot.append({**s, "injected": inj})
            
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
