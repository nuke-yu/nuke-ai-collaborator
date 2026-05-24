import asyncio
from pathlib import Path
from datetime import date, datetime

WORKSPACE_ROOT = Path(__file__).parent / "workspaces"

_SUBDIRS = ["skills", "logs"]


def bot_workspace(bot_id: int) -> Path:
    path = WORKSPACE_ROOT / f"bot_{bot_id}"
    path.mkdir(parents=True, exist_ok=True)
    for sub in _SUBDIRS:
        (path / sub).mkdir(exist_ok=True)
    return path


def group_workspace(group_id: int) -> Path:
    path = WORKSPACE_ROOT / f"group_{group_id}" / "shared"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_path(workspace: Path, relative: str) -> Path | None:
    try:
        resolved = (workspace / relative).resolve()
        if resolved.is_relative_to(workspace.resolve()):
            return resolved
    except Exception:
        pass
    return None


async def read_file(bot_id: int, path: str) -> str:
    ws = bot_workspace(bot_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.exists():
        return f"[文件不存在] {path}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"[读取错误] {e}"


async def write_file(bot_id: int, path: str, content: str) -> str:
    ws = bot_workspace(bot_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {path}（{len(content)} 字符）"


async def list_workspace(bot_id: int) -> str:
    ws = bot_workspace(bot_id)
    lines = []
    for p in sorted(ws.rglob("*")):
        rel = p.relative_to(ws)
        indent = "  " * (len(rel.parts) - 1)
        icon = "📁" if p.is_dir() else "📄"
        lines.append(f"{indent}{icon} {p.name}")
    return "\n".join(lines) if lines else "（工作区为空）"


# ---------------------------------------------------------------------------
# Skill helpers — directory-first, flat-file fallback
# ---------------------------------------------------------------------------

def _skill_path(skills_dir: Path, name: str) -> tuple[Path | None, str]:
    """Return (path, kind) for a skill. Directory structure takes priority."""
    dir_skill = skills_dir / name / "SKILL.md"
    if dir_skill.exists():
        return dir_skill, "md"
    flat_md = skills_dir / f"{name}.md"
    if flat_md.exists():
        return flat_md, "md"
    flat_py = skills_dir / f"{name}.py"
    if flat_py.exists():
        return flat_py, "py"
    return None, ""


def _parse_frontmatter(content: str) -> dict:
    """Extract fields from YAML frontmatter (--- delimited). Returns {} if none."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm: dict = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key == "name":
            fm["name"] = val
        elif key == "description":
            fm["description"] = val
        elif key == "when_to_use":
            fm["when_to_use"] = val
        elif key == "always":
            fm["always"] = val.lower() in ("true", "yes", "1")
    return fm


def _parse_skill_meta(path: Path) -> dict:
    """Return {description, always} for a skill file. Frontmatter takes priority."""
    try:
        content = path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        description = fm.get("description", "")
        always = fm.get("always", False)
        if not description:
            # Fallback: first non-empty line outside frontmatter
            in_fm = content.startswith("---")
            skipped_open = False
            for line in content.splitlines():
                if line.strip() == "---":
                    if not skipped_open:
                        skipped_open = True
                        continue
                    in_fm = False
                    continue
                if in_fm:
                    continue
                clean = line.strip().lstrip("#").strip()
                if clean:
                    description = clean
                    break
        return {"description": description, "always": always,
                "when_to_use": fm.get("when_to_use", "")}
    except Exception:
        return {"description": "", "always": False}


def list_skills(bot_id: int) -> list[dict]:
    """Return available skills as [{name, type, description, always}].
    Parses frontmatter for description and always flag.
    Scans both directory-style (name/SKILL.md) and flat files (name.md/.py).
    """
    ws = bot_workspace(bot_id)
    skills_dir = ws / "skills"
    if not skills_dir.exists():
        return []
    seen: set = set()
    result = []

    for p in sorted(skills_dir.iterdir()):
        if p.is_dir():
            skill_file = p / "SKILL.md"
            if skill_file.exists():
                seen.add(p.name)
                meta = _parse_skill_meta(skill_file)
                result.append({"name": p.name, "type": "md", **meta})
        elif p.suffix == ".md" and p.stem not in seen:
            seen.add(p.stem)
            meta = _parse_skill_meta(p)
            result.append({"name": p.stem, "type": "md", **meta})
        elif p.suffix == ".py" and p.stem not in seen:
            seen.add(p.stem)
            result.append({"name": p.stem, "type": "py",
                           "description": "(代码技能，M3)", "always": False})

    return result


def load_always_skills(bot_id: int) -> list[dict]:
    """Return full content for skills with always: true — [{name, content}]."""
    ws = bot_workspace(bot_id)
    result = []
    for skill in list_skills(bot_id):
        if not skill.get("always"):
            continue
        path, kind = _skill_path(ws / "skills", skill["name"])
        if path and kind == "md":
            try:
                result.append({"name": skill["name"], "content": path.read_text(encoding="utf-8")})
            except Exception:
                pass
    return result


async def run_skill(bot_id: int, name: str, args: str = "") -> str:
    ws = bot_workspace(bot_id)
    path, kind = _skill_path(ws / "skills", name)
    if path is None:
        available = [s["name"] for s in list_skills(bot_id)]
        hint = f"，当前可用：{available}" if available else "，skills/ 目录为空"
        return f"[未找到技能 '{name}']{hint}"
    if kind == "py":
        return f"[技能 {name}.py 需要代码沙箱支持（M3 实现）]"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Context loading — hierarchical (group-level → bot-level)
# ---------------------------------------------------------------------------

def _read_md(path: Path) -> str | None:
    if path.exists():
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


async def load_context_files(bot_id: int, group_id: int | None,
                             file_names: list[str]) -> list[dict]:
    """Return context blocks as [{source, name, content}].

    Loading order (later overrides earlier for same filename):
      1. bot workspace files
      2. group shared files (same names, if group_id given)

    Returned as a list so callers can inject as user-message prefix
    rather than stuffing into system prompt.
    """
    blocks = []
    bot_ws = bot_workspace(bot_id)
    group_ws = group_workspace(group_id) if group_id else None

    for name in file_names:
        bot_content = _read_md(bot_ws / name)
        group_content = group_ws and _read_md(group_ws / name)

        if bot_content:
            blocks.append({"source": "bot", "name": name, "content": bot_content})
        if group_content:
            # Group-level file appended after (or replaces context for that name)
            blocks.append({"source": "group", "name": name, "content": group_content})

    return blocks


def format_context_blocks(blocks: list[dict]) -> str:
    """Format context blocks into a readable string for injection."""
    parts = []
    for b in blocks:
        label = b["name"] if b["source"] == "bot" else f"{b['name']} (群组)"
        parts.append(f"=== {label} ===\n{b['content']}")
    return "\n\n".join(parts)


# legacy shim so simple_v1 keeps working
async def read_startup_files(bot_id: int, file_names: list[str]) -> str:
    blocks = await load_context_files(bot_id, None, file_names)
    return format_context_blocks(blocks)


# ---------------------------------------------------------------------------
# Workspace init
# ---------------------------------------------------------------------------

async def init_bot_workspace(bot: dict):
    """Create default workspace files for a newly created bot."""
    bot_id = bot["id"]
    name = bot.get("name", "Bot")
    role = bot.get("role", "")
    system_prompt = (bot.get("system_prompt") or "").strip()
    personality_prompt = (bot.get("personality_prompt") or "").strip()

    ws = bot_workspace(bot_id)

    identity = f"# {name}\n\n**角色：** {role}\n\n{system_prompt or f'你是 {name}，{role}。'}\n"
    soul = f"# {name} · 行事原则\n\n{personality_prompt or '- 诚实、专业、高效。'}\n"
    bootstrap = f"# 启动指令\n\n每次对话开始时，回顾工作区状态，确认当前任务优先级。\n"
    agent = f"""# AGENT.md — {name} 的推理框架

## 角色定位
{role or name}

## 思考方式

在回应之前，先在脑海中完成以下步骤：

1. **理解意图** — 对方真正想要的是什么？表面需求背后有没有更深的目标？
2. **盘点已知** — 我现在掌握哪些信息？工作区里有什么可以参考？
3. **识别缺口** — 还缺什么信息？是否需要先澄清，还是可以合理推断？
4. **选择行动** — 直接回答 / 调用工具 / 请求补充信息，哪种最有效？
5. **验证输出** — 我的回答是否真的解决了问题？有没有遗漏边界条件？

## 工作原则

- 优先完成，再求完美
- 遇到不确定时，明说假设，而不是沉默或胡猜
- 主动更新工作区文件，保持状态可追溯
- 每次任务结束写入日志

## 边界

- 不在没有充分理由的情况下修改他人负责的文件
- 不超出当前任务范围擅自扩展
"""

    for filename, content in [
        ("IDENTITY.md", identity),
        ("SOUL.md", soul),
        ("BOOTSTRAP.md", bootstrap),
        ("AGENT.md", agent),
    ]:
        p = ws / filename
        if not p.exists():
            p.write_text(content, encoding="utf-8")


def list_workspace_tree(bot_id: int) -> list[dict]:
    """Return file tree as list of {path, name, is_dir} for UI."""
    ws = bot_workspace(bot_id)
    result = []
    for p in sorted(ws.rglob("*")):
        rel = str(p.relative_to(ws))
        result.append({"path": rel, "name": p.name, "is_dir": p.is_dir()})
    return result


async def init_group_workspace(group_id: int, group_name: str = ""):
    """Create default shared workspace files for a newly created group."""
    ws = group_workspace(group_id)
    (ws / "deliverables").mkdir(exist_ok=True)

    display = group_name or f"群组 {group_id}"
    today = date.today().isoformat()

    board = f"""# 工作看板 · {display}

更新时间：{today}

## Backlog
| # | 需求 | 优先级 |
|---|------|--------|

## 进行中
| # | 需求 | 负责人 | 状态 | Todo |
|---|------|--------|------|------|

## 已完成
| # | 需求 | 负责人 | 完成时间 | 产出 |
|---|------|--------|---------|------|
"""

    spec = f"""# 需求文档 · {display}

> 由 PM Bot 维护，记录项目背景、目标和详细需求。

## 项目背景


## 核心需求


## 验收标准

"""

    for filename, content in [
        ("BOARD.md", board),
        ("SPEC.md", spec),
    ]:
        p = ws / filename
        if not p.exists():
            p.write_text(content, encoding="utf-8")


async def init_all_bots(bots: list[dict]):
    """Backfill workspace files for all existing bots that don't have them yet."""
    for bot in bots:
        if bot.get("type") == "bot":
            await init_bot_workspace(bot)


async def append_log(bot_id: int, entry: str):
    """Append a timestamped entry to today's log file (non-blocking)."""
    ws = bot_workspace(bot_id)
    log_file = ws / "logs" / f"{date.today().isoformat()}.md"
    ts = datetime.now().strftime("%H:%M")
    text = f"\n## {ts}\n\n{entry.strip()}\n"

    def _write():
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(text)

    await asyncio.to_thread(_write)
