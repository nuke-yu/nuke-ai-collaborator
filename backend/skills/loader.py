import os
import platform

from . import constants as C
from .metadata import skill_path, parse_skill_meta, strip_context_window_suffix
from .discovery import list_skills, list_skills_all, available_skills_for_bot
from .processor import process_skill_content
from workspace import layout

# Budget for always-on (常驻) skill bodies injected into every system prompt.
# Without a cap, many/large always-skills silently balloon the prompt.
_MAX_SKILL_BODY_CHARS = 8000       # per-skill full-body cap
_MAX_ALWAYS_TOTAL_CHARS = 24000    # total budget across all always-skills
_MAX_COMPANION_FILES = 10          # cap directory-skill companion listing


def _fold_home(text: str) -> str:
    """Collapse the host home-dir prefix to ~ (token saving + less path leak)."""
    home = os.path.expanduser("~")
    return text.replace(home, "~") if home and home != "~" else text


def _skills_dir_for_layer(layer: str, bot_id: int,
                           group_id: int | None, role: str | None):
    """Return the skills directory Path for a given layer.

    Reads ``skills.constants`` live so test-time monkeypatching of the canonical
    constants is honored. L3 role resolves under group_<id>/roles/<role>/skills
    (group-internal, cross-group isolated).
    """
    if layer == "system":
        return C.SYSTEM_SKILLS_ROOT
    if layer == "group" and group_id:
        return layout.group_shared_dir(group_id) / "skills"
    if layer == "role" and role and group_id:
        return layout.group_roles_dir(group_id) / role / "skills"
    if layer == "role":
        return None
    if layer == "learned":
        return C.bot_ws(bot_id, group_id) / "skills" / "learned" / "active"
    return C.bot_ws(bot_id, group_id) / "skills"


async def load_always_skills(bot_id: int, group_id: int | None = None,
                       role: str | None = None) -> list[dict]:
    """Return full content for skills with always: true across all four layers.

    Enforces a prompt budget: a body over _MAX_SKILL_BODY_CHARS, or one that
    would overflow the _MAX_ALWAYS_TOTAL_CHARS running total, is DEGRADED to a
    short summary (full text still reachable via run_skill). Skills are visited
    in layer order (system first), so foundational layers keep priority.
    """
    skills = await available_skills_for_bot(bot_id, group_id=group_id, role=role)
    result = []
    total = 0
    for skill in skills:
        if not skill.get("always"):
            continue
        # A3: Use the pre-resolved path from the discovery layer (handles stub fallbacks)
        path = skill.get("path")
        kind = skill.get("type", "md")
        if not (path and kind == "md" and path.exists()):
            continue
        try:
            body = _fold_home(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        name = skill["name"]
        remaining = _MAX_ALWAYS_TOTAL_CHARS - total
        if len(body) > _MAX_SKILL_BODY_CHARS or len(body) > remaining:
            desc = skill.get("description", "")
            summary = (
                f"（常驻技能「{name}」正文 {len(body)} 字符，超出注入预算，"
                f"此处仅注入摘要；需要全文请调用 run_skill(name=\"{name}\")）"
                + (f"\n摘要：{desc}" if desc else "")
            )
            result.append({"name": name, "content": summary, "degraded": True})
            total += len(summary)
            continue

        result.append({"name": name, "content": body})
        total += len(body)
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
    available_skills = await available_skills_for_bot(bot_id, group_id=group_id, role=role)
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

    # Env-aware templating is opt-in (`template: true`); only then do we expose a
    # small set of SAFE variables to the sandboxed Jinja2 renderer. Gating on the
    # flag avoids mangling existing skills that contain literal `{{ }}`.
    template_vars = None
    if skill_entry.get("template"):
        template_vars = {
            "os": platform.system(),
            "is_windows": os.name == "nt",
            "shell": "powershell" if os.name == "nt" else "/bin/sh",
            "role": role or "",
            "group_id": group_id,
            "bot_id": bot_id,
            "args": args,
        }

    # Base directory header + full transformation pipeline
    skill_dir_norm = str(skill_dir).replace("\\", "/")
    content = f"Base directory for this skill: {skill_dir_norm}\n\n{raw}"
    content = await process_skill_content(content, skill_dir, args=args, template_vars=template_vars)

    # Companion files (directory skills only)
    if path.name == "SKILL.md":
        companions = sorted(
            f for f in skill_dir.iterdir()
            if f.name != "SKILL.md" and not f.name.startswith('.')
        )
        if companions:
            shown = companions[:_MAX_COMPANION_FILES]
            lines = [f"  {f}" for f in shown]
            overflow = len(companions) - len(shown)
            if overflow > 0:
                lines.append(f"  …还有 {overflow} 个文件（已省略）")
            file_list = "\n".join(lines)
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
                "model": strip_context_window_suffix(skill_entry.get("model", "")),
            }
            return "__SKILL_FORK__"
        if skill_entry.get("allowed_tools"):
            ctx["skill_allowed_tools"] = skill_entry["allowed_tools"]
        if skill_entry.get("model"):
            ctx["skill_model"] = strip_context_window_suffix(skill_entry["model"])

    # Inline framing: tell the model this is an instruction set to execute now.
    # The fork path returned "__SKILL_FORK__" earlier, so it is never wrapped.
    return (
        "<skill_instructions>\n"
        f"{content}\n"
        "\n现在请按以上技能描述的步骤开始执行。\n"
        "</skill_instructions>"
    )
