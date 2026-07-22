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
          VALUES (?,?,?,?, 'trial','S0',1,?,?) ON CONFLICT(skill_id) DO UPDATE SET updated_at=excluded.updated_at""",
          (skill_id,group_id,row[0],name,now,now))
        await db.execute("""INSERT INTO skill_versions
          (skill_id,version,declaration_json,content_hash,evidence_ids,created_at) VALUES (?,?,?,?,?,?)
          ON CONFLICT(skill_id,version) DO NOTHING""",
          (skill_id,1,canonical,digest,row[5],now))
        await db.commit()
    return skill_id


async def promote_skill(skill_id: str, group_id: int, target_maturity: str = "active") -> bool:
    """Explicit promotion gate for trial skills (e.g. shadow evaluation or human approval)."""
    if target_maturity not in {"active", "stable"}:
        raise ValueError("Invalid target maturity for promotion")
    from ai.memory import _memory_db
    now = int(time.time() * 1000)
    async with await _memory_db("skills", group_id, write=True) as db:
        cur = await db.execute("UPDATE skills SET maturity=?, updated_at=? WHERE skill_id=? AND group_id=?",
                               (target_maturity, now, skill_id, group_id))
        await db.commit()
        return cur.rowcount == 1


async def recall_skills(*, query: str, run_id: str, group_id: int | None,
                        bot_id: int | None, limit: int = 2) -> tuple[str,list[str]]:
    if group_id is None:
        return "",[]
    from ai.memory import _memory_db
    from ai.experiences import _terms
    async with await _memory_db("skills",group_id,write=False) as db:
        async with db.execute("""SELECT s.skill_id,s.current_version,v.declaration_json
          FROM skills s JOIN skill_versions v ON v.skill_id=s.skill_id AND v.version=s.current_version
          WHERE s.group_id=? AND s.bot_id=? AND s.status='active' AND s.maturity IN ('trial','active','stable')""",
          (group_id,bot_id)) as cur:
            rows=await cur.fetchall()
    q=_terms(query); ranked=[]
    for skill_id,version,raw in rows:
        declaration=json.loads(raw); terms=_terms(declaration.get("trigger",""))
        score=len(q&terms)/max(1,len(q|terms))
        if score: ranked.append((score,skill_id,version,declaration))
    ranked.sort(reverse=True); selected=ranked[:limit]; now=int(time.time()*1000)
    if not selected: return "",[]
    async with await _memory_db("skill_usage",group_id,write=True) as db:
        for _,skill_id,version,_ in selected:
            await db.execute("INSERT INTO skill_usage(skill_id,version,run_id,group_id,created_at) VALUES(?,?,?,?,?) "
                             "ON CONFLICT(skill_id,run_id) DO NOTHING",(skill_id,version,run_id,group_id,now))
        await db.commit()
    body=[]
    for _,_,_,d in selected:
        body.append(
            f"<untrusted_learned_skill>\n"
            f"Trigger pattern: \"{d.get('trigger', '')}\"\n"
            f"Procedure: " + "; ".join(d.get("procedure", [])) + "\n"
            f"</untrusted_learned_skill>"
        )
    return "[Verified declarative skills]\n"+"\n".join(body),[x[1] for x in selected]


async def complete_skill_usage(*, skill_ids:list[str],run_id:str,group_id:int|None,
                               outcome:str) -> None:
    if group_id is None or not skill_ids:return
    from ai.memory import _memory_db
    now=int(time.time()*1000)
    async with await _memory_db("skills",group_id,write=True) as db:
        for skill_id in skill_ids:
            cur = await db.execute("UPDATE skill_usage SET outcome=?, state='executed' WHERE skill_id=? AND run_id=? AND state!='executed'",
                             (outcome,skill_id,run_id))
            if cur.rowcount == 1:
                if outcome=="completed":
                    await db.execute("""UPDATE skills SET success_count=success_count+1,
                      maturity=CASE WHEN maturity='trial' THEN 'active'
                        WHEN maturity='active' AND success_count+1>=3 THEN 'stable' ELSE maturity END,
                      updated_at=? WHERE skill_id=?""",(now,skill_id))
                else:
                    await db.execute("""UPDATE skills SET failure_count=failure_count+1,
                      status=CASE WHEN failure_count+1>=2 THEN 'suspended' ELSE status END,
                      updated_at=? WHERE skill_id=?""",(now,skill_id))
        await db.commit()
    for skill_id in skill_ids:
        await project_skill(skill_id,group_id)


async def project_skill(skill_id:str,group_id:int) -> str|None:
    """Trusted canonical-to-file projection; generated content cannot execute code."""
    from ai.memory import _memory_db
    async with await _memory_db("skills",group_id,write=False) as db:
        async with db.execute("""SELECT s.bot_id,s.name,s.maturity,s.status,s.current_version,v.declaration_json
          FROM skills s JOIN skill_versions v ON v.skill_id=s.skill_id AND v.version=s.current_version
          WHERE s.skill_id=? AND s.group_id=?""",(skill_id,group_id)) as cur:
            row=await cur.fetchone()
    if not row:return None
    from skills.constants import bot_ws
    from skills.lifecycle import file_lock
    folder="active" if row[2] in {"active","stable"} and row[3]=="active" else "draft"
    learned=bot_ws(row[0],group_id)/"skills"/"learned"
    path=learned/folder/f"{row[1]}.md"
    alternate=learned/("draft" if folder=="active" else "active")/f"{row[1]}.md"
    declaration=json.loads(row[5]); validate_declaration(declaration)
    content=(f"---\nname: {row[1]}\nlayer: learned\nstatus: {row[2]}\n"
             f"risk_level: {declaration['risk_level']}\ncanonical_skill_id: {skill_id}\nversion: {row[4]}\n---\n\n"
             f"## Trigger\n\n{declaration['trigger']}\n\n## Procedure\n\n"+
             "\n".join(f"{i+1}. {step}" for i,step in enumerate(declaration["procedure"]))+"\n")
    with file_lock(path):
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content,encoding="utf-8")
    if alternate.exists():
        with file_lock(alternate):
            if alternate.exists(): alternate.unlink()
    return str(path)
