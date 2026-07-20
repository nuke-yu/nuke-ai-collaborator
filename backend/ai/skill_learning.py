"""Canonical declarative learned skills. Workspace files are projections only."""
from __future__ import annotations
import hashlib
import json
import re
import time

_SAFE_TOOL = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,79}$")
_BANNED = {"run_shell", "bash", "shell", "eval", "exec"}


def validate_declaration(value: dict) -> None:
    risk = value.get("risk_level")
    if risk not in {"S0", "S1"}:
        raise ValueError("only declarative S0/S1 skills may be compiled")
    if not value.get("trigger") or not value.get("procedure"):
        raise ValueError("skill requires trigger and procedure")
    tools = value.get("allowed_tools") or []
    if risk == "S0" and tools:
        raise ValueError("S0 skills cannot call tools")
    if any(not _SAFE_TOOL.match(t) or t in _BANNED for t in tools):
        raise ValueError("unsafe or executable tool in learned skill")
    encoded = json.dumps(value, ensure_ascii=False).lower()
    if "bypasspermissions" in encoded or "bypass_permissions" in encoded:
        raise ValueError("permission bypass is forbidden")


async def compile_candidate(record_id: str, group_id: int) -> str | None:
    from ai.memory import _memory_db
    async with await _memory_db("memory_records", group_id, write=False) as db:
        async with db.execute("SELECT bot_id,content,task_signature,confidence,supporting_count,source_ids "
                              "FROM memory_records WHERE record_id=? AND group_id=? AND kind='experience' AND status='active'",
                              (record_id,group_id)) as cur:
            row = await cur.fetchone()
    if not row or row[3] < 0.7 or row[4] < 2:
        return None
    experience = json.loads(row[1])
    declaration = {
        "risk_level":"S0", "trigger":experience.get("task_pattern", ""),
        "procedure":["Review the prior failure mode before planning", "Apply the verified corrective lesson"],
        "verification":[experience.get("verification", "run_terminal_completed")],
        "limitations":experience.get("limitations", ""), "allowed_tools":[],
    }
    validate_declaration(declaration)
    skill_id = "skill:" + hashlib.sha256(f"{group_id}:{row[0]}:{row[2]}".encode()).hexdigest()[:24]
    name = f"learned-{row[2]}"
    canonical = json.dumps(declaration,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest(); now = int(time.time()*1000)
    async with await _memory_db("skills", group_id, write=True) as db:
        await db.execute("""INSERT INTO skills
          (skill_id,group_id,bot_id,name,maturity,risk_level,current_version,created_at,updated_at)
          VALUES (?,?,?,?, 'candidate','S0',1,?,?) ON CONFLICT(skill_id) DO UPDATE SET updated_at=excluded.updated_at""",
          (skill_id,group_id,row[0],name,now,now))
        await db.execute("""INSERT INTO skill_versions
          (skill_id,version,declaration_json,content_hash,evidence_ids,created_at) VALUES (?,?,?,?,?,?)
          ON CONFLICT(skill_id,version) DO NOTHING""",
          (skill_id,1,canonical,digest,row[5],now))
        await db.commit()
    return skill_id
