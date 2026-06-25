# backend/skills/sources/_scan.py
import os
from pathlib import Path
from typing import List
from ..metadata import parse_skill_meta
from .base import SkillEntry


def scan_dir(path: Path, layer: str) -> List[SkillEntry]:
    """Identical to the historical discovery._scan_dir_sync."""
    if not path.exists():
        return []
    seen: set = set()
    result: List[SkillEntry] = []
    for p in sorted(path.iterdir()):
        if p.is_dir():
            sf = p / "SKILL.md"
            if sf.exists() and p.name not in seen:
                seen.add(p.name)
                meta = parse_skill_meta(sf)
                meta["layer"] = meta.get("layer") or layer
                result.append({"name": p.name, "type": "md", "path": sf, **meta})
        elif p.suffix == ".md" and p.stem not in seen:
            seen.add(p.stem)
            meta = parse_skill_meta(p)
            meta["layer"] = meta.get("layer") or layer
            result.append({"name": p.stem, "type": "md", "path": p, **meta})
        elif p.suffix == ".py" and p.stem not in seen:
            seen.add(p.stem)
            result.append({
                "name": p.stem, "type": "py", "layer": layer,
                "description": "(代码技能)", "always": False,
                "status": "active", "when_to_use": "", "learns": False,
                "is_stub": False, "fm_keys": [], "path": p
            })
    return result


def dir_signature(path: Path) -> list:
    sig = []
    if not path.exists():
        return sig
    for root, _, files in os.walk(path):
        for fn in files:
            if fn.endswith((".md", ".py")):
                fp = os.path.join(root, fn)
                try:
                    st = os.stat(fp)
                    sig.append((fp, st.st_mtime_ns, st.st_size))
                except OSError:
                    continue
    return sig
