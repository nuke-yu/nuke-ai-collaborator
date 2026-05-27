from .constants import bot_ws
from .metadata import skill_path, parse_skill_meta
from .discovery import list_skills
from .processor import process_skill_content


def load_always_skills(bot_id: int) -> list[dict]:
    """Return full content for skills with always: true — [{name, content}]."""
    ws = bot_ws(bot_id)
    result = []
    for skill in list_skills(bot_id):
        if not skill.get("always"):
            continue
        path, kind = skill_path(ws / "skills", skill["name"])
        if path and kind == "md":
            try:
                result.append({"name": skill["name"], "content": path.read_text(encoding="utf-8")})
            except Exception:
                pass
    return result


async def run_skill(bot_id: int, name: str, args: str = "", ctx: dict | None = None) -> str:
    """Load a skill and return its processed prompt content.

    Applies the full processor pipeline (argument substitution, ${SKILL_DIR},
    shell command embedding) then appends companion file listing for directory
    skills.  Sets ctx side-effect keys for the executor.
    """
    ws = bot_ws(bot_id)
    path, kind = skill_path(ws / "skills", name)
    if path is None:
        available = [s["name"] for s in list_skills(bot_id)]
        hint = f"，当前可用：{available}" if available else "，skills/ 目录为空"
        return f"[未找到技能 '{name}']{hint}"
    if kind == "py":
        return f"[{name}.py] 请使用 run_shell 执行此脚本：{path}"

    raw = path.read_text(encoding="utf-8")
    skill_dir = path.parent
    meta = parse_skill_meta(path)

    # Base directory header + full transformation pipeline
    content = f"Base directory for this skill: {skill_dir}\n\n{raw}"
    content = await process_skill_content(
        content, skill_dir,
        args=args,
        use_powershell=meta.get("shell") == "powershell",
    )

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

    # Executor side-effects
    if ctx is not None:
        if meta.get("max_iterations"):
            ctx["skill_max_iterations"] = meta["max_iterations"]
        if meta.get("learns"):
            ctx["skill_learns"] = name
        if meta.get("context") == "fork":
            ctx["skill_fork"] = {
                "name": name,
                "content": content,
                "args": args,
                "allowed_tools": meta.get("allowed_tools", []),
                "model": meta.get("model", ""),
            }
            return "__SKILL_FORK__"
        if meta.get("allowed_tools"):
            ctx["skill_allowed_tools"] = meta["allowed_tools"]
        if meta.get("model"):
            ctx["skill_model"] = meta["model"]

    return content
