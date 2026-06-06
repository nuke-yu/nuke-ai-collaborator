import os
from pathlib import Path
import yaml


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
    
    frontmatter_text = "\n".join(lines[1:end])
    try:
        raw_fm = yaml.safe_load(frontmatter_text)
    except Exception:
        return {}
        
    if not isinstance(raw_fm, dict):
        return {}
        
    fm: dict = {}
    
    # Process standard string fields
    for str_key in ["name", "description", "when_to_use", "status", "layer", "paths", "context", "shell", "model"]:
        if str_key in raw_fm:
            fm[str_key] = str(raw_fm[str_key]) if raw_fm[str_key] is not None else ""
            
    # For argument-hint (support both '-' and '_' naming variants)
    arg_hint = raw_fm.get("argument-hint") or raw_fm.get("argument_hint")
    if arg_hint is not None:
        fm["argument_hint"] = str(arg_hint)
        
    # Process boolean fields
    for bool_key in ["always", "learns"]:
        if bool_key in raw_fm:
            val = raw_fm[bool_key]
            if isinstance(val, bool):
                fm[bool_key] = val
            else:
                fm[bool_key] = str(val).lower() in ("true", "yes", "1")
                
    # Process integer iterations
    if "max_iterations" in raw_fm:
        val = raw_fm["max_iterations"]
        try:
            fm["max_iterations"] = int(val)
        except (ValueError, TypeError):
            pass
            
    # Process user-invocable flag
    user_invocable = raw_fm.get("user-invocable") or raw_fm.get("user_invocable")
    if user_invocable is not None:
        if isinstance(user_invocable, bool):
            fm["user_invocable"] = user_invocable
        else:
            fm["user_invocable"] = str(user_invocable).lower() not in ("false", "no", "0")
            
    # Process allowed-tools (A2: list list/comma dual state)
    allowed_tools = raw_fm.get("allowed-tools") or raw_fm.get("allowed_tools")
    if allowed_tools is not None:
        if isinstance(allowed_tools, list):
            fm["allowed_tools"] = [str(t).strip() for t in allowed_tools if t]
        elif isinstance(allowed_tools, str):
            fm["allowed_tools"] = [t.strip() for t in allowed_tools.split(",") if t.strip()]
        else:
            fm["allowed_tools"] = []
            
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
                    
        body = strip_frontmatter(content).strip()
        is_stub = not body

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
            "is_stub": is_stub,
            "fm_keys": list(fm.keys())
        }
    except Exception:
        return {"description": "", "always": False, "status": "active",
                "layer": "", "learns": False, "is_stub": False, "fm_keys": []}
