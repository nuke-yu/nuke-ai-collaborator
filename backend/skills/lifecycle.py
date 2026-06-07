import sys
import time
from pathlib import Path
from contextlib import contextmanager
from .constants import LEARNED_DRAFT, LEARNED_ACTIVE, bot_ws
from .metadata import skill_path, parse_frontmatter, _is_safe_name


@contextmanager
def file_lock(file_path: Path):
    """Acquire an exclusive lock on the file_path to protect write operations."""
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    try:
        fd = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            for _ in range(50):
                try:
                    msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise OSError(f"Could not acquire lock on {lock_path} (Windows)")
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fd:
            try:
                if sys.platform != "win32":
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fd.close()
            except Exception:
                pass


def write_to_draft(bot_id: int, skill_name: str, content: str) -> str:
    """Write a skill directly to learned/draft/."""
    if not _is_safe_name(skill_name):
        return "[非法技能名]"
    ws = bot_ws(bot_id)
    draft_path = ws / LEARNED_DRAFT / f"{skill_name}.md"
    with file_lock(draft_path):
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(content, encoding="utf-8")
    return skill_name


def update_skill_status(bot_id: int, skill_name: str, new_status: str) -> str:
    """Toggle a skill's status. Creates a personal override stub for L1/L2/L3 skills."""
    if not _is_safe_name(skill_name):
        return "[非法技能名]"
    ws = bot_ws(bot_id)
    path, _ = skill_path(ws / "skills", skill_name)

    if path is None:
        override_path = ws / "skills" / "manual" / f"{skill_name}.md"
        if new_status == "active":
            return f"['{skill_name}' 在上层定义，默认已启用，无需覆盖]"
        with file_lock(override_path):
            override_path.parent.mkdir(parents=True, exist_ok=True)
            stub = f"---\nname: {skill_name}\nlayer: personal\nstatus: {new_status}\n---\n"
            override_path.write_text(stub, encoding="utf-8")
        return f"已在 personal 层创建覆盖文件，{skill_name} 状态设为 {new_status}"

    if new_status == "active":
        try:
            with file_lock(path):
                content_check = path.read_text(encoding="utf-8").strip()
                fm = parse_frontmatter(content_check)
                body = content_check.split("---", 2)[-1].strip() if content_check.count("---") >= 2 else ""
                if not body and set(fm.keys()) <= {"name", "layer", "status"}:
                    path.unlink()
                    return f"已删除覆盖文件，{skill_name} 恢复上层定义（active）"
        except Exception:
            pass

    try:
        with file_lock(path):
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if lines and lines[0].strip() == "---":
                end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
                if end:
                    fm_lines = lines[1:end]
                    new_fm, found = [], False
                    for line in fm_lines:
                        if line.strip().startswith("status:"):
                            new_fm.append(f"status: {new_status}")
                            found = True
                        else:
                            new_fm.append(line)
                    if not found:
                        new_fm.append(f"status: {new_status}")
                    new_content = "---\n" + "\n".join(new_fm) + "\n---\n" + "\n".join(lines[end + 1:])
                    path.write_text(new_content, encoding="utf-8")
                    return f"已更新 {skill_name} 状态为 {new_status}"
            return "[frontmatter 格式错误]"
    except Exception as e:
        return f"[错误] {e}"


def approve_draft_skill(bot_id: int, skill_name: str) -> str:
    """Move a skill from learned/draft/ to learned/active/."""
    if not _is_safe_name(skill_name):
        return "[非法技能名]"
    ws = bot_ws(bot_id)
    draft_dir = ws / LEARNED_DRAFT
    active_dir = ws / "skills" / "learned" / "active"
    active_dir.mkdir(parents=True, exist_ok=True)
    src, _ = skill_path(draft_dir, skill_name)
    if src is None:
        return f"[未找到草稿技能 '{skill_name}']"
    
    dest = active_dir / src.name
    with file_lock(src):
        with file_lock(dest):
            src.rename(dest)
    return f"已审批通过：{skill_name} 移入 active/"


def reject_draft_skill(bot_id: int, skill_name: str) -> str:
    """Delete a skill from learned/draft/."""
    if not _is_safe_name(skill_name):
        return "[非法技能名]"
    ws = bot_ws(bot_id)
    src, _ = skill_path(ws / LEARNED_DRAFT, skill_name)
    if src is None:
        return f"[未找到草稿技能 '{skill_name}']"
    with file_lock(src):
        src.unlink()
    return f"已拒绝并删除草稿：{skill_name}"
