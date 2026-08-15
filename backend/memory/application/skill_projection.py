"""Canonical Skill declaration to workspace projection."""
from __future__ import annotations

import json
from typing import Any
from memory.contracts import MemoryOperationError
from memory.application.context import require_database
from memory.domain.safety import safe_memory_text
from memory.ports import MemoryDatabasePort


class CanonicalSkillProjectionService:
    """Project an existing canonical Skill without changing its authority."""

    def __init__(self, database: MemoryDatabasePort | None = None,
                 workspace: Any | None = None) -> None:
        self._database = database or require_database()
        self._workspace = workspace

    async def project(self, skill_id: str, group_id: int) -> str:
        async with await self._database.connect("skills", group_id, write=False) as db:
            async with db.execute(
                """SELECT s.bot_id,s.name,s.maturity,s.status,s.current_version,
                          v.declaration_json
                   FROM skills s JOIN skill_versions v
                     ON v.skill_id=s.skill_id AND v.version=s.current_version
                   WHERE s.skill_id=? AND s.group_id=?""",
                (skill_id, group_id),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            raise MemoryOperationError(f"Skill not found for projection: {skill_id}")
        bot_id, name, maturity, status, version, raw_declaration = row
        name = str(name)
        if not _safe_name(name):
            raise MemoryOperationError("canonical Skill has unsafe projection name")
        try:
            declaration = json.loads(raw_declaration or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MemoryOperationError("canonical Skill declaration is invalid JSON") from exc
        _validate_declaration(declaration)

        folder = "active" if str(maturity) in {"active", "stable"} and str(status) == "active" else "draft"
        content = (
            f"---\nname: {name}\nlayer: learned\nstatus: {maturity}\n"
            f"risk_level: {declaration['risk_level']}\ncanonical_skill_id: {skill_id}\n"
            f"version: {int(version)}\n---\n\n## Trigger\n\n{safe_memory_text(declaration['trigger'])}\n\n"
            "## Procedure\n\n" + "\n".join(
                f"{index + 1}. {safe_memory_text(step)}"
                for index, step in enumerate(declaration["procedure"])
            ) + "\n"
        )
        if self._workspace is None:
            from memory.application.context import require_skill_workspace
            self._workspace = require_skill_workspace()
        return self._workspace.write_skill(
            group_id=group_id, bot_id=int(bot_id), name=name,
            folder=folder, content=content,
        )


def _safe_name(name: str) -> bool:
    return bool(name) and len(name) <= 80 and all(
        char.isalnum() or char in "-_" for char in name
    )


def _validate_declaration(value: dict) -> None:
    if value.get("risk_level") not in {"S0", "S1"}:
        raise MemoryOperationError("only declarative S0/S1 skills may be projected")
    if not str(value.get("trigger") or "").strip() or not value.get("procedure"):
        raise MemoryOperationError("Skill requires trigger and procedure")
    if value.get("risk_level") == "S0" and value.get("allowed_tools"):
        raise MemoryOperationError("S0 skills cannot call tools")
    banned = {"run_shell", "bash", "shell", "eval", "exec"}
    for tool in value.get("allowed_tools") or []:
        if not isinstance(tool, str) or tool in banned:
            raise MemoryOperationError("unsafe executable tool in Skill declaration")
