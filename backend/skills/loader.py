from .constants import bot_ws, WORKSPACE_ROOT, SYSTEM_SKILLS_ROOT, ROLES_ROOT
from .metadata import skill_path, parse_skill_meta
from .discovery import list_skills, list_skills_all
from .processor import process_skill_content


def _skills_dir_for_layer(layer: str, bot_id: int,
                           group_id: int | None, role: str | None):
    """Return the skills directory Path for a given layer."""
    if layer == "system":
        return SYSTEM_SKILLS_ROOT
    if layer == "group" and group_id:
        return WORKSPACE_ROOT / f"group_{group_id}" / "shared" / "skills"
    if layer == "role" and role:
        return ROLES_ROOT / role / "skills"
    if layer == "learned":
        return bot_ws(bot_id) / "skills" / "learned" / "active"
    return bot_ws(bot_id) / "skills"


async def load_always_skills(bot_id: int, group_id: int | None = None,
                       role: str | None = None) -> list[dict]:
    """Return full content for skills with always: true across all four layers."""
    skills = await list_skills_all(bot_id, group_id=group_id, role=role)
    result = []
    for skill in skills:
        if not skill.get("always"):
            continue
        # A3: Use the pre-resolved path from the discovery layer (handles stub fallbacks)
        path = skill.get("path")
        kind = skill.get("type", "md")
        if path and kind == "md" and path.exists():
            try:
                result.append({"name": skill["name"], "content": path.read_text(encoding="utf-8")})
            except Exception:
                pass
    return result


async def run_skill(bot_id: int, name: str, args: str = "", ctx: dict | None = None) -> str:
    """Load a skill and return its processed prompt content.

    Applies the full processor pipeline (argument substitution, ${SKILL_DIR},
    shell command embedding) then appends companion file listing for directory
    skills. Sets ctx side-effect keys for the executor.
    """
    group_id = ctx.get("group_id") if ctx else None
    role = ctx.get("role") if ctx else None

    # Resolve from all layers dynamically (A3 Fallback support)
    available_skills = await list_skills_all(bot_id, group_id=group_id, role=role)
    skill_entry = next((s for s in available_skills if s["name"] == name), None)

    if not skill_entry:
        available_names = [s["name"] for s in available_skills if s.get("status") not in ("disabled", "deprecated")]
        hint = f"，当前可用：{available_names}" if available_names else "，可用技能列表为空"
        return f"[未找到技能 '{name}']{hint}"

    path = skill_entry.get("path")
    kind = skill_entry.get("type", "md")

    if not path or not path.exists():
        return f"[未找到技能 '{name}']"

    if kind == "py":
        return f"[{name}.py] 请使用 run_shell 执行此脚本：{path}"

    raw = path.read_text(encoding="utf-8")
    skill_dir = path.parent

    # Base directory header + full transformation pipeline
    content = f"Base directory for this skill: {skill_dir}\n\n{raw}"
    content = await process_skill_content(content, skill_dir, args=args)

    # Companion files (directory skills only)
    if path.name == "SKILL.md":
        companions = sorted(
            f for f in skill_dir.iterdir()
            if f.name != "SKILL.md" and not f.name.startswith('.')
        )
        if companions:
            file_list = "\n".join(f"  {f}" for f in companions)
            content += (
                f"\n\n<skill_files>\n{file_list}\n</skill_files>"
                "\nRelative paths in this skill are relative to the base directory above."
            )

    # Executor side-effects (read from merged entry to support overrides in personal stubs)
    if ctx is not None:
        if skill_entry.get("max_iterations"):
            ctx["skill_max_iterations"] = skill_entry["max_iterations"]
        if skill_entry.get("learns"):
            ctx["skill_learns"] = name
        if skill_entry.get("context") == "fork":
            ctx["skill_fork"] = {
                "name": name,
                "content": content,
                "args": args,
                "allowed_tools": skill_entry.get("allowed_tools", []),
                "model": skill_entry.get("model", ""),
            }
            return "__SKILL_FORK__"
        if skill_entry.get("allowed_tools"):
            ctx["skill_allowed_tools"] = skill_entry["allowed_tools"]
        if skill_entry.get("model"):
            ctx["skill_model"] = skill_entry["model"]

    return content
