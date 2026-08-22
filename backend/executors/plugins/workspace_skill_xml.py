"""Lazy skill-list XML rendering with model-aware budget limits."""
from __future__ import annotations

SKILL_DESC_MAX_CHARS = 250


def build_skills_xml(skills: list[dict], model_name: str) -> tuple[str, set[str]]:
    from executors import compact
    context_window = compact._MODEL_CONTEXT_WINDOWS.get(model_name, compact._DEFAULT_CONTEXT_WINDOW)
    budget = max(3000, int(context_window * 0.01 * 4))
    parts: list[str] = []
    used = 0
    included: set[str] = set()
    skipped = 0
    for skill in skills:
        desc = (skill.get("description") or "")[:SKILL_DESC_MAX_CHARS]
        lines = [f"    <name>{skill['name']}</name>", f"    <description>{desc}</description>"]
        if skill.get("when_to_use"):
            lines.append(f"    <when_to_use>{skill['when_to_use']}</when_to_use>")
        if skill.get("argument_hint"):
            lines.append(f"    <argument_hint>{skill['argument_hint']}</argument_hint>")
        snippet = "  <skill>\n" + "\n".join(lines) + "\n  </skill>"
        if used + len(snippet) > budget:
            skipped += 1
            continue
        parts.append(snippet)
        used += len(snippet)
        included.add(skill["name"])
    if not parts:
        return "", included
    xml = "<available_skills>\n" + "\n".join(parts) + "\n</available_skills>"
    if skipped:
        xml += f"\n<!-- 另有 {skipped} 个技能因 token 预算未列出 -->"
    return xml, included
