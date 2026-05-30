import os
from pathlib import Path


def _is_safe_name(name: str) -> bool:
    """Reject names that could escape skills_dir (path separators, parent refs)."""
    if not name or name != name.strip():
        return False
    if os.path.isabs(name):
        return False
    return not ("/" in name or "\\" in name or ".." in name or "\x00" in name)


def _contained(base: Path, target: Path) -> bool:
    """True if target, fully resolved, stays inside base (blocks symlink escapes too)."""
    try:
        return target.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


def skill_path(skills_dir: Path, name: str) -> tuple[Path | None, str]:
    """Return (path, kind) for a skill. Directory structure takes priority.

    Defense-in-depth against path traversal: the model-supplied ``name`` is
    rejected if it contains path separators / parent refs, and every candidate
    must resolve to a location inside ``skills_dir``. The primary guard lives in
    ``loader.run_skill`` (name must be a discovered skill).
    """
    if not _is_safe_name(name):
        return None, ""
    dir_skill = skills_dir / name / "SKILL.md"
    if dir_skill.exists() and _contained(skills_dir, dir_skill):
        return dir_skill, "md"
    flat_md = skills_dir / f"{name}.md"
    if flat_md.exists() and _contained(skills_dir, flat_md):
        return flat_md, "md"
    flat_py = skills_dir / f"{name}.py"
    if flat_py.exists() and _contained(skills_dir, flat_py):
        return flat_py, "py"
    return None, ""


def parse_frontmatter(content: str) -> dict:
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
        elif key == "max_iterations":
            try:
                fm["max_iterations"] = int(val)
            except ValueError:
                pass
        elif key == "status":
            fm["status"] = val
        elif key == "layer":
            fm["layer"] = val
        elif key == "learns":
            fm["learns"] = val.lower() in ("true", "yes", "1")
        elif key == "paths":
            fm["paths"] = val
        elif key == "context":
            fm["context"] = val  # "inline" (default) or "fork"
        elif key == "shell":
            fm["shell"] = val  # "bash" (default) or "powershell"
        elif key == "user-invocable":
            fm["user_invocable"] = val.lower() not in ("false", "no", "0")
        elif key == "argument-hint":
            fm["argument_hint"] = val
        elif key == "allowed-tools":
            fm["allowed_tools"] = [t.strip() for t in val.split(",") if t.strip()]
        elif key == "model":
            fm["model"] = val
    return fm


def strip_frontmatter(content: str) -> str:
    """Return content with YAML frontmatter (--- block) removed."""
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return content
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "".join(lines[i + 1:]).lstrip("\n")
    return content


def parse_skill_meta(path: Path) -> dict:
    """Return metadata dict for a skill file. Frontmatter takes priority."""
    try:
        content = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        description = fm.get("description", "")
        always = fm.get("always", False)
        if not description:
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
        return {
            "description": description,
            "always": always,
            "when_to_use": fm.get("when_to_use", ""),
            "max_iterations": fm.get("max_iterations"),
            "status": fm.get("status", "active"),
            "layer": fm.get("layer", ""),
            "learns": fm.get("learns", False),
            "paths": fm.get("paths", ""),
            "context": fm.get("context", "inline"),
            "shell": fm.get("shell", "bash"),
            "user_invocable": fm.get("user_invocable", True),
            "argument_hint": fm.get("argument_hint", ""),
            "allowed_tools": fm.get("allowed_tools", []),
            "model": fm.get("model", ""),
        }
    except Exception:
        return {"description": "", "always": False, "status": "active",
                "layer": "", "learns": False}
