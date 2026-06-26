# backend/skills/store.py
import shutil
from pathlib import Path
from .metadata import _is_safe_name
from .lifecycle import file_lock
from .sources._scan import scan_dir
from .constants import HIGH_PRIVILEGE_TOOLS


def _resolved_skill_path(scope_dir: Path, name: str) -> Path:
    # Check if a directory skill exists and contains SKILL.md
    dir_path = scope_dir / name
    if (dir_path / "SKILL.md").exists():
        return dir_path / "SKILL.md"
    return scope_dir / f"{name}.md"


class SkillStore:
    def list(self, scope) -> list:
        return scan_dir(scope.dir(), getattr(scope, "layer", "scope"))

    def read(self, scope, name: str) -> str:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        return _resolved_skill_path(scope.dir(), name).read_text(encoding="utf-8")

    def write(self, scope, name: str, content: str) -> dict:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        sdir = scope.dir()
        dir_path = sdir / name
        if (dir_path / "SKILL.md").exists() or dir_path.is_dir():
            fp = dir_path / "SKILL.md"
        else:
            fp = sdir / f"{name}.md"
            
        with file_lock(fp):
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            
        low = content.lower()
        # Intentionally differs from composer's C2 check: store scans raw `content` by
        # substring because it has no parsed metadata yet; composer scans parsed
        # `allowed_tools` + body. They are not duplicates of the same logic.
        flagged = [t for t in HIGH_PRIVILEGE_TOOLS if t in low]
        return {"name": name, "high_privilege": flagged}

    def delete(self, scope, name: str) -> None:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        sdir = scope.dir()
        dir_path = sdir / name
        if dir_path.is_dir():
            with file_lock(dir_path / "SKILL.md"):
                shutil.rmtree(dir_path)
        else:
            fp = sdir / f"{name}.md"
            with file_lock(fp):
                if fp.exists():
                    fp.unlink()

    def copy(self, src, name: str, dst) -> None:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        src_dir = src.dir()
        dst_dir = dst.dir()
        
        src_folder = src_dir / name
        dst_folder = dst_dir / name
        
        if src_folder.is_dir():
            with file_lock(dst_folder / "SKILL.md"):
                if dst_folder.exists():
                    shutil.rmtree(dst_folder)
                shutil.copytree(src_folder, dst_folder, symlinks=False)
        else:
            s = src_dir / f"{name}.md"
            d = dst_dir / f"{name}.md"
            if not s.exists():
                raise FileNotFoundError(f"source skill not found: {name!r}")
            with file_lock(d):
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)  # copy2 preserves mtime
