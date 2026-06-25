# backend/skills/store.py
import shutil
from pathlib import Path
from .metadata import _is_safe_name
from .lifecycle import file_lock
from .sources._scan import scan_dir
from .constants import HIGH_PRIVILEGE_TOOLS


def _skill_file(scope, name: str) -> Path:
    return scope.dir() / f"{name}.md"


class SkillStore:
    def list(self, scope) -> list:
        return scan_dir(scope.dir(), getattr(scope, "layer", "scope"))

    def read(self, scope, name: str) -> str:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        return _skill_file(scope, name).read_text(encoding="utf-8")

    def write(self, scope, name: str, content: str) -> dict:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        fp = _skill_file(scope, name)
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
        fp = _skill_file(scope, name)
        with file_lock(fp):
            # Idempotent by design: deleting an absent skill is a no-op, not an
            # error (desired end-state is "absent"). copy() fails loud on a
            # missing source because it cannot reach its end-state without one.
            if fp.exists():
                fp.unlink()

    def copy(self, src, name: str, dst) -> None:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        s = _skill_file(src, name)
        d = _skill_file(dst, name)
        with file_lock(d):
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)  # copy2 preserves mtime
