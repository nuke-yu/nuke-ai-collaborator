"""Current project adapter for learned Skill workspace projection."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


class CurrentSkillWorkspace:
    def write_skill(self, *, group_id: int, bot_id: int, name: str,
                    folder: str, content: str) -> str:
        from skills.constants import bot_ws
        from skills.lifecycle import file_lock

        learned = bot_ws(int(bot_id), group_id) / "skills" / "learned"
        path = learned / folder / f"{name}.md"
        alternate = learned / ("draft" if folder == "active" else "active") / f"{name}.md"
        learned.mkdir(parents=True, exist_ok=True)
        projection_lock = learned / f".{name}.projection"
        temp_path: str | None = None
        with file_lock(projection_lock):
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=path.parent,
                    prefix=f".{path.name}.", suffix=".tmp", delete=False,
                ) as temp_file:
                    temp_file.write(content)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                    temp_path = temp_file.name
                os.replace(temp_path, path)
                temp_path = None
                if alternate.exists():
                    alternate.unlink()
            finally:
                if temp_path is not None:
                    try:
                        os.unlink(temp_path)
                    except FileNotFoundError:
                        pass
        return str(path)
