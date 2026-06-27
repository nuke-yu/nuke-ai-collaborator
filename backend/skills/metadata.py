import os
from pathlib import Path
import re
import yaml


def _is_safe_name(name: str) -> bool:
    """Reject names that don't match the strict alphanumeric/dash/underscore pattern."""
    if not name:
        return False
    return bool(re.match(r"^[a-z0-9_-]+$", name))


def strip_context_window_suffix(model: str) -> str:
    """Drop a trailing [1m] long-context-window marker from a skill's model id.

    Authors may write `model: claude-opus-4-8[1m]` to request the 1M-token
    window; we strip the marker so the bare id resolves normally (the window
    request itself is not yet wired into providers).
    """
    m = (model or "").strip()
    if m.endswith("[1m]"):
        return m[:-4].strip()
    return m


def _contained(base: Path, target: Path) -> bool:
    """True if target, fully resolved, stays inside base (blocks symlink escapes too)."""
    try:
        return target.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


def skill_path(skills_dir: Path, name: str) -> tuple[Path | None, str]:
    """Return (path, kind) for a skill. Directory structure takes priority.

    Defense-in-depth against path traversal: the model-supplied ``name`` is
    rejected if it doesn't match the regex whitelist, and every candidate
    must resolve to a location inside ``skills_dir``. The primary guard lives in
    ``loader.run_skill`` (name must be a discovered skill).
    """
    if not _is_safe_name(name):
        return None, ""

    # Check potential subdirectories: manual/ for personal/overrides, learned/active/ for learned, or root.
    candidates = [
        skills_dir / "manual",
        skills_dir / "learned" / "active",
        skills_dir
    ]

    for base_dir in candidates:
        if not base_dir.exists():
            continue
        dir_skill = base_dir / name / "SKILL.md"
        if dir_skill.exists() and _contained(skills_dir, dir_skill):
            return dir_skill, "md"
        flat_md = base_dir / f"{name}.md"
        if flat_md.exists() and _contained(skills_dir, flat_md):
            return flat_md, "md"
        flat_py = base_dir / f"{name}.py"
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
    for str_key in ["name", "description", "when_to_use", "status", "layer", "paths", "context", "shell", "model", "platforms", "version"]:
        if str_key in raw_fm:
            fm[str_key] = str(raw_fm[str_key]) if raw_fm[str_key] is not None else ""
            
    # For argument-hint (support both '-' and '_' naming variants)
    arg_hint = raw_fm.get("argument-hint") or raw_fm.get("argument_hint")
    if arg_hint is not None:
        fm["argument_hint"] = str(arg_hint)
        
    # Process boolean fields
    for bool_key in ["always", "learns", "template"]:
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

    # Process roles (support list / comma separated string)
    roles = raw_fm.get("roles") or raw_fm.get("role")
    if roles is not None:
        if isinstance(roles, list):
            fm["roles"] = [str(r).strip().lower() for r in roles if r]
        elif isinstance(roles, str):
            fm["roles"] = [r.strip().lower() for r in roles.split(",") if r.strip()]
        else:
            fm["roles"] = []

    # Process stages (support list / comma separated string)
    stages = raw_fm.get("stages") or raw_fm.get("stage")
    if stages is not None:
        if isinstance(stages, list):
            fm["stages"] = [str(s).strip().lower() for s in stages if s]
        elif isinstance(stages, str):
            fm["stages"] = [s.strip().lower() for s in stages.split(",") if s.strip()]
        else:
            fm["stages"] = []
            
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
            "template": fm.get("template", False),
            "paths": fm.get("paths", ""),
            "context": fm.get("context", "inline"),
            "shell": fm.get("shell", "bash"),
            "platforms": fm.get("platforms", "pure"),
            "version": fm.get("version", ""),
            "user_invocable": fm.get("user_invocable", True),
            "argument_hint": fm.get("argument_hint", ""),
            "allowed_tools": fm.get("allowed_tools", []),
            "model": fm.get("model", ""),
            "roles": fm.get("roles", []),
            "stages": fm.get("stages", []),
            "is_stub": is_stub,
            "fm_keys": list(fm.keys())
        }
    except Exception:
        return {"description": "", "always": False, "status": "active",
                "layer": "", "learns": False, "is_stub": False, "fm_keys": [],
                "roles": [], "stages": []}


# path -> (mtime_ns, parsed meta). Bounded by the number of SKILL.md files on
# disk (itself capped by the deployment's scale ceiling), so it cannot grow
# without bound. Concurrent readers may redundantly parse-and-store the same
# key; that is idempotent (same value, last write wins) so no lock is needed.
_META_CACHE: dict[str, tuple[int, dict]] = {}


def parse_skill_meta_cached(path: Path) -> dict:
    """`parse_skill_meta` with an mtime-invalidated cache.

    Serves an unchanged file from cache and re-parses only when its mtime
    moves — so a hot read path (e.g. the member-skills GET description join,
    hit on every panel open) doesn't re-read+parse every SKILL.md each time,
    while edits to a skill still surface immediately. A missing/inaccessible
    file falls back to the empty-meta result and is NOT cached (cheap to
    re-stat; lets a later-created file populate naturally).

    Returns the cached dict by reference; treat the result as read-only.
    """
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return parse_skill_meta(path)
    key = str(path)
    cached = _META_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    meta = parse_skill_meta(path)
    _META_CACHE[key] = (mtime, meta)
    return meta
