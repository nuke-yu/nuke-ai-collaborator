"""User-facing tool-loop progress and active-skill formatting."""
from __future__ import annotations

THINKING_I18N = {
    "zh": {
        "templates": ["分析用户需求和当前任务状态...", "检查之前的执行记录...", "评估可用的工具和方法...", "制定下一步执行计划...", "确认工具调用的参数和预期结果...", "验证上一步的执行结果...", "整理最终回复内容..."],
        "iter_1": "iteration {iter_count}: {template_0}",
        "iter_2": "iteration {iter_count}: 上一步完成了 {completed}，需要继续...",
        "iter_2_fallback": "初步分析",
        "iter_other": "iteration {iter_count}: 继续执行剩余任务，整合结果...",
    },
    "en": {
        "templates": ["Analyzing user requirements and current task status...", "Checking previous execution history...", "Evaluating available tools and methods...", "Formulating next step plan...", "Confirming tool call parameters and expected results...", "Verifying previous execution results...", "Formatting final response content..."],
        "iter_1": "iteration {iter_count}: {template_0}",
        "iter_2": "iteration {iter_count}: Previous step completed {completed}, continuing...",
        "iter_2_fallback": "initial analysis",
        "iter_other": "iteration {iter_count}: Continuing with remaining tasks, consolidating results...",
    },
}


def generate_thinking_preview(runner, iter_count: int) -> str:
    from workspace.layout import get_group_language
    labels = THINKING_I18N.get(get_group_language(runner.ctx.group_id), THINKING_I18N["zh"])
    tool_names = [record["name"] for record in runner.tool_records[-3:]] if runner.tool_records else []
    if iter_count == 1:
        return labels["iter_1"].format(iter_count=iter_count, template_0=labels["templates"][0])
    if iter_count == 2:
        completed = ", ".join(tool_names[:2]) if tool_names else labels["iter_2_fallback"]
        return labels["iter_2"].format(iter_count=iter_count, completed=completed)
    return labels["iter_other"].format(iter_count=iter_count)


def build_invoked_skills_block(invoked_skills: dict, budget: int = 6000) -> str:
    if not invoked_skills:
        return ""
    parts: list[str] = []
    remaining = budget
    for name, body in reversed(list(invoked_skills.items())):
        if remaining <= 0:
            break
        snippet = body[:remaining]
        parts.append(f'<active_skill name="{name}">\n{snippet}\n</active_skill>')
        remaining -= len(snippet)
    return "\n\n".join(parts)
