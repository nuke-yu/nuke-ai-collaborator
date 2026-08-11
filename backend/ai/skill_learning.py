"""Canonical declarative learned skills. Workspace files are projections only."""
from __future__ import annotations
import hashlib
import json
import os
import re
import tempfile
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
    executable_fields = {"code", "python", "shell", "shell_command", "executable"}
    if executable_fields.intersection(value):
        raise ValueError("executable code fields are forbidden in learned skills")
    procedure_values = value.get("procedure") if isinstance(value.get("procedure"), list) else [value.get("procedure")]
    procedure_text = " ".join(str(item) for item in procedure_values).lower()
    if any(marker in procedure_text for marker in ("os.system(", "subprocess.", "eval(", "exec(", "curl |", "bash -c")):
        raise ValueError("executable instructions are forbidden in learned skills")
    encoded = json.dumps(value, ensure_ascii=False).lower()
    if "bypasspermissions" in encoded or "bypass_permissions" in encoded:
        raise ValueError("permission bypass is forbidden")


def _projection_input_version(
    version: int, maturity: str, status: str
) -> str:
    return f"{version}:{maturity}:{status}"


async def _enqueue_skill_projection(
    db,
    *,
    skill_id: str,
    group_id: int,
    version: int,
    maturity: str,
    status: str,
    reopen_completed: bool = False,
) -> str:
    from memory.application.jobs import pipeline_job_identity

    input_version = _projection_input_version(version, maturity, status)
    job_id, key = pipeline_job_identity(
        "project_skill", group_id, skill_id, input_version
    )
    now = int(time.time() * 1000)
    await db.execute(
        """INSERT INTO pipeline_jobs
           (job_id,job_type,group_id,input_id,input_version,
            idempotency_key,created_at,updated_at)
           VALUES (?,'project_skill',?,?,?,?,?,?)
           ON CONFLICT(idempotency_key) DO NOTHING""",
        (
            job_id,
            group_id,
            skill_id,
            input_version,
            key,
            now,
            now,
        ),
    )
    if reopen_completed:
        await db.execute(
            """UPDATE pipeline_jobs
               SET status='pending',attempt=0,lease_until=NULL,
                   lease_token=NULL,error='',output_json='{}',updated_at=?,
                   completed_at=NULL
               WHERE idempotency_key=? AND status='completed'""",
            (now, key),
        )
    return job_id


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

    # Voyager-style environmental critic gate.  Experience verification is a
    # necessary signal, but Skill compilation must also inspect the persisted
    # attempts that produced it.  This prevents a stale or malformed
    # Experience row from becoming a reusable Skill without a final clean
    # execution state.
    source_case_ids = tuple(json.loads(row[5] or "[]"))
    everos_cluster_id = ""
    everos_cluster_size = 0
    everos_skill_md = ""
    everos_qualification_score = 0.0
    everos_tools_sequence: tuple[str, ...] = ()
    if source_case_ids:
        from memory.adapters.algorithms import VoyagerCriticEngine
        from memory.adapters.algorithms.everos_case_engine import ExtractedCase
        from memory.adapters.algorithms.everos_clustering_engine import EverOSClusteringEngine
        from memory.adapters.algorithms.everos_skill_engine import EverOSSkillEngine

        critic = VoyagerCriticEngine()
        clustering_cases = []
        async with await _memory_db("agent_cases", group_id, write=False) as cases_db:
            for source_case_id in source_case_ids:
                async with cases_db.execute(
                    """SELECT task,outcome,errors,tools_used,files_touched,
                              outcome_confidence,verification_signals,summary,
                              correction_evidence_json,created_at FROM agent_cases
                       WHERE case_id=? AND group_id=?""",
                    (str(source_case_id), group_id),
                ) as case_cur:
                    case_row = await case_cur.fetchone()
                if not case_row:
                    return None
                try:
                    case_errors = tuple(json.loads(case_row[2] or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    case_errors = ()
                try:
                    case_tools = tuple(json.loads(case_row[3] or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    case_tools = ()
                try:
                    case_files = tuple(json.loads(case_row[4] or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    case_files = ()
                try:
                    verification_signals = tuple(json.loads(case_row[6] or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    verification_signals = ()
                try:
                    correction_evidence = json.loads(case_row[8] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    correction_evidence = {}
                clustering_cases.append(
                    (
                        ExtractedCase(
                            case_id=str(source_case_id),
                            task=str(case_row[0] or ""),
                            task_signature=str(row[2] or ""),
                            tools_used=case_tools,
                            files_touched=case_files,
                            errors=case_errors,
                            outcome=str(case_row[1] or ""),
                            outcome_confidence=float(case_row[5] or 0.0),
                            verification_signals=verification_signals,
                            information_gain="high",
                            should_distill=True,
                            summary=str(case_row[7] or ""),
                            correction_evidence=correction_evidence,
                        ),
                        float(case_row[9] or 0) / 1000.0,
                    )
                )
                async with cases_db.execute(
                    """SELECT action_tool,observation_status,observation_summary
                       FROM agent_case_attempts
                       WHERE case_id=? AND group_id=? ORDER BY ordinal""",
                    (str(source_case_id), group_id),
                ) as attempt_cur:
                    attempt_rows = await attempt_cur.fetchall()
                tool_records = [
                    {
                        "name": str(attempt[0] or ""),
                        "is_error": str(attempt[1] or "") == "error",
                        "result": str(attempt[2] or ""),
                    }
                    for attempt in attempt_rows
                ]
                critic_result = critic.evaluate_success(
                    str(case_row[0] or ""),
                    str(case_row[1] or ""),
                    tool_records,
                    case_errors,
                )
                if not critic_result.passed:
                    return None

        if clustering_cases:
            clusters = EverOSClusteringEngine().cluster_cases(clustering_cases)
            if not clusters:
                return None
            selected_cluster = max(clusters, key=lambda cluster: len(cluster.cases))
            everos_cluster_id = selected_cluster.cluster_id
            everos_cluster_size = len(selected_cluster.cases)
            # Existing Nuke qualification gates remain authoritative.  The
            # EverOS engine supplies the richer induction artifact and score.
            induced = EverOSSkillEngine(
                min_cases=2, min_success_rate=0.8, min_qualification_score=0.7
            ).compile_skill_candidate(selected_cluster)
            if induced is not None:
                everos_skill_md = induced.skill_md_content
                everos_qualification_score = induced.qualification_score
                everos_tools_sequence = induced.tools_sequence

    declaration = {
        "risk_level":"S0", "trigger":experience.get("task_pattern", ""),
        "procedure":["Review the prior failure mode before planning", "Apply the verified corrective lesson"],
        "verification":[experience.get("verification", "run_terminal_completed")],
        "limitations":experience.get("limitations", ""), "allowed_tools":[],
        "provenance": {
            "everos_cluster_id": everos_cluster_id,
            "everos_cluster_size": everos_cluster_size,
            "source_case_ids": list(source_case_ids),
        },
        "everos_induction": {
            "skill_md": everos_skill_md,
            "qualification_score": everos_qualification_score,
            "tools_sequence": list(everos_tools_sequence),
        },
    }
    from memory.adapters.algorithms import VoyagerCriticEngine
    execution_plan = VoyagerCriticEngine.compile_execution_plan(declaration)
    declaration["execution_plan"] = {
        "trigger": execution_plan.trigger,
        "steps": list(execution_plan.steps),
        "allowed_tools": list(execution_plan.allowed_tools),
        "verification": list(execution_plan.verification),
        "requires_hil": execution_plan.requires_hil,
    }
    validate_declaration(declaration)
    skill_id = "skill:" + hashlib.sha256(f"{group_id}:{row[0]}:{row[2]}".encode()).hexdigest()[:24]
    name = f"learned-{row[2]}"
    canonical = json.dumps(declaration,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest(); now = int(time.time()*1000)
    async with await _memory_db("skills", group_id, write=True) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS everos_source_documents (
                source_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
                record_id TEXT NOT NULL, source_type TEXT NOT NULL,
                content_json TEXT NOT NULL, created_at INTEGER NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_everos_source_documents_group
               ON everos_source_documents(group_id,record_id,created_at)"""
        )
        source_document_id = "everos-source:" + hashlib.sha256(
            f"{group_id}:{record_id}:{row[5]}".encode()
        ).hexdigest()[:24]
        await db.execute(
            """INSERT OR REPLACE INTO everos_source_documents
               (source_id,group_id,record_id,source_type,content_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (source_document_id, group_id, record_id, "experience_case_snapshot",
             json.dumps({"experience": experience, "source_case_ids": list(source_case_ids)},
                        ensure_ascii=False, sort_keys=True), now),
        )
        await db.execute("""INSERT INTO skills
          (skill_id,group_id,bot_id,name,maturity,risk_level,current_version,created_at,updated_at)
          VALUES (?,?,?,?, 'trial','S0',1,?,?) ON CONFLICT(skill_id) DO UPDATE SET updated_at=excluded.updated_at""",
          (skill_id,group_id,row[0],name,now,now))
        await db.execute("""INSERT INTO skill_versions
          (skill_id,version,declaration_json,content_hash,evidence_ids,created_at) VALUES (?,?,?,?,?,?)
          ON CONFLICT(skill_id,version) DO NOTHING""",
          (skill_id,1,canonical,digest,row[5],now))
        async with db.execute(
            """SELECT current_version,maturity,status FROM skills
               WHERE skill_id=? AND group_id=?""",
            (skill_id, group_id),
        ) as cur:
            projection = await cur.fetchone()
        await _enqueue_skill_projection(
            db,
            skill_id=skill_id,
            group_id=group_id,
            version=int(projection[0]),
            maturity=str(projection[1]),
            status=str(projection[2]),
        )
        await db.commit()
    return skill_id


async def list_skill_candidates(
    *, group_id: int, bot_id: int
) -> list[dict]:
    """Return reviewable canonical trial Skills for one Group-owned Bot."""
    from ai.memory import _memory_db

    async with await _memory_db("skills", group_id, write=False) as db:
        async with db.execute(
            """SELECT s.skill_id,s.name,s.maturity,s.risk_level,
                      s.current_version,s.success_count,s.failure_count,
                      v.declaration_json,v.evidence_ids
               FROM skills s
               JOIN skill_versions v
                 ON v.skill_id=s.skill_id AND v.version=s.current_version
               WHERE s.group_id=? AND s.bot_id=? AND s.status='active'
                 AND s.maturity='trial'
               ORDER BY s.updated_at DESC,s.skill_id""",
            (group_id, bot_id),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "skill_id": str(row[0]),
            "name": str(row[1]),
            "maturity": str(row[2]),
            "risk_level": str(row[3]),
            "version": int(row[4]),
            "success_count": int(row[5]),
            "failure_count": int(row[6]),
            "declaration": json.loads(row[7]),
            "evidence_ids": tuple(json.loads(row[8] or "[]")),
        }
        for row in rows
    ]


async def promote_skill(
    skill_id: str,
    group_id: int,
    target_maturity: str = "active",
    *,
    bot_id: int | None = None,
    actor_id: str,
    reason: str,
) -> bool:
    """Apply an explicit human promotion with immutable audit evidence."""
    if target_maturity not in {"active", "stable"}:
        raise ValueError("Invalid target maturity for promotion")
    if not actor_id.startswith("user:") or not actor_id[5:].isdigit():
        raise ValueError("Promotion requires a human user actor_id")
    if not reason.strip():
        raise ValueError("Promotion reason is required")
    from ai.memory import _memory_db
    now = int(time.time() * 1000)
    expected_prev = "trial" if target_maturity == "active" else "active"
    async with await _memory_db("skills", group_id, write=True) as db:
        sql = """UPDATE skills SET maturity=?, updated_at=?
                 WHERE skill_id=? AND group_id=? AND status='active'
                   AND maturity=?"""
        params: tuple = (
            target_maturity,
            now,
            skill_id,
            group_id,
            expected_prev,
        )
        if bot_id is not None:
            sql += " AND bot_id=?"
            params += (bot_id,)
        cur = await db.execute(sql, params)
        if cur.rowcount == 1:
            await db.execute("""INSERT INTO skill_promotion_audit
              (skill_id,group_id,actor_id,reason,from_maturity,to_maturity,created_at)
              VALUES (?,?,?,?,?,?,?)""",
              (skill_id,group_id,actor_id,reason,expected_prev,target_maturity,now))
            async with db.execute(
                """SELECT current_version,maturity,status FROM skills
                   WHERE skill_id=? AND group_id=?""",
                (skill_id, group_id),
            ) as projection_cur:
                projection = await projection_cur.fetchone()
            await _enqueue_skill_projection(
                db,
                skill_id=skill_id,
                group_id=group_id,
                version=int(projection[0]),
                maturity=str(projection[1]),
                status=str(projection[2]),
            )
        await db.commit()
        return cur.rowcount == 1


async def recall_skills(*, query: str, run_id: str, group_id: int | None,
                        bot_id: int | None, limit: int = 2) -> tuple[str, list[str]]:
    if group_id is None:
        return "", []
    from ai.memory import _memory_db
    from ai.experiences import _terms

    q = _terms(query)
    if not q:
        return "", []

    async with await _memory_db("skills", group_id, write=False) as db:
        async with db.execute(
            """SELECT s.skill_id, s.current_version, s.maturity, v.declaration_json
               FROM skills s
               JOIN skill_versions v ON v.skill_id=s.skill_id AND v.version=s.current_version
               WHERE s.group_id=? AND (s.bot_id=? OR s.bot_id IS NULL)
                 AND s.status='active' AND s.maturity IN ('trial', 'active', 'stable')""",
            (group_id, bot_id),
        ) as cur:
            rows = await cur.fetchall()

    maturity_weights = {"stable": 1.0, "active": 0.9, "trial": 0.7}
    ranked = []

    for skill_id, version, maturity, raw in rows:
        declaration = json.loads(raw)
        trigger_text = str(declaration.get("trigger", ""))
        name_text = str(declaration.get("name", ""))
        terms = _terms(f"{name_text} {trigger_text}")
        lexical = len(q & terms) / max(1, len(q | terms)) if terms else 0.0
        weight = maturity_weights.get(maturity, 0.7)
        score = lexical * weight

        if score >= 0.08 or lexical >= 0.15:
            ranked.append((score, skill_id, version, declaration))

    ranked.sort(reverse=True)
    selected = ranked[:limit]
    if not selected:
        return "", []

    now = int(time.time() * 1000)
    async with await _memory_db("skill_usage", group_id, write=True) as db:
        for _, skill_id, version, _ in selected:
            await db.execute(
                "INSERT INTO skill_usage(skill_id,version,run_id,group_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(skill_id,run_id) DO NOTHING",
                (skill_id, version, run_id, group_id, now, now),
            )
        await db.commit()

    body = []
    from memory.application.references import skill_ref
    for _, skill_id, version, d in selected:
        clean_trigger = str(d.get("trigger", "")).replace("</untrusted_learned_skill>", "")
        clean_procedure = "; ".join(
            str(step).replace("</untrusted_learned_skill>", "")
            for step in d.get("procedure", [])
        )
        body.append(
            f'<untrusted_learned_skill memory_ref="{skill_ref(skill_id, version)}">\n'
            f'Trigger pattern: "{clean_trigger}"\n'
            f"Procedure: {clean_procedure}\n"
            f"</untrusted_learned_skill>"
        )
    return "[Learned declarative skills]\n" + "\n".join(body), [x[1] for x in selected]


async def resolve_skill_refs(
    *, skill_ids: list[str], group_id: int, bot_id: int
) -> tuple[str, ...]:
    """Resolve canonical IDs to their current injected version without parsing text."""
    if not skill_ids:
        return ()
    from ai.memory import _memory_db
    from memory.application.references import skill_ref

    placeholders = ",".join("?" for _ in skill_ids)
    async with await _memory_db("skills", group_id, write=False) as db:
        async with db.execute(
            f"""SELECT skill_id,current_version FROM skills
                WHERE group_id=? AND bot_id=? AND status='active'
                  AND maturity IN ('active','stable')
                  AND skill_id IN ({placeholders})""",
            (group_id, bot_id, *skill_ids),
        ) as cur:
            rows = await cur.fetchall()
    versions = {str(skill_id): int(version) for skill_id, version in rows}
    return tuple(
        skill_ref(skill_id, versions[skill_id])
        for skill_id in skill_ids
        if skill_id in versions
    )


async def complete_skill_usage(*, skill_ids:list[str],run_id:str,group_id:int|None,
                               outcome:str) -> None:
    """Compatibility telemetry: run completion alone cannot mature a Skill."""

    from ai.usage_tracking import record_legacy_completion
    from memory.domain import UsageKind
    await record_legacy_completion(
        kind=UsageKind.SKILL,
        item_ids=skill_ids,
        run_id=run_id,
        group_id=group_id,
        outcome=outcome,
    )


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
    from skills.metadata import _is_safe_name
    if not _is_safe_name(str(row[1])):
        raise ValueError("canonical Skill has unsafe projection name")
    folder="active" if row[2] in {"active","stable"} and row[3]=="active" else "draft"
    learned=bot_ws(row[0],group_id)/"skills"/"learned"
    path=learned/folder/f"{row[1]}.md"
    alternate=learned/("draft" if folder=="active" else "active")/f"{row[1]}.md"
    declaration=json.loads(row[5]); validate_declaration(declaration)
    content=(f"---\nname: {row[1]}\nlayer: learned\nstatus: {row[2]}\n"
             f"risk_level: {declaration['risk_level']}\ncanonical_skill_id: {skill_id}\nversion: {row[4]}\n---\n\n"
             f"## Trigger\n\n{declaration['trigger']}\n\n## Procedure\n\n"+
             "\n".join(f"{i+1}. {step}" for i,step in enumerate(declaration["procedure"]))+"\n")
    learned.mkdir(parents=True, exist_ok=True)
    projection_lock = learned / f".{row[1]}.projection"
    temp_path = None
    with file_lock(projection_lock):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
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


def _projection_matches(
    *,
    bot_id: int,
    group_id: int,
    name: str,
    maturity: str,
    status: str,
    version: int,
    skill_id: str,
) -> bool:
    from skills.constants import bot_ws
    from skills.metadata import _is_safe_name

    if not _is_safe_name(name):
        return False
    folder = (
        "active"
        if maturity in {"active", "stable"} and status == "active"
        else "draft"
    )
    learned = bot_ws(bot_id, group_id) / "skills" / "learned"
    path = learned / folder / f"{name}.md"
    alternate = learned / (
        "draft" if folder == "active" else "active"
    ) / f"{name}.md"
    try:
        header = path.read_text(encoding="utf-8")[:2048]
    except (OSError, UnicodeError):
        return False
    return (
        f"canonical_skill_id: {skill_id}\n" in header
        and f"version: {version}\n" in header
        and not alternate.exists()
    )


async def enqueue_missing_skill_projections(group_id: int) -> int:
    """Repair canonical commit → workspace projection gaps."""
    from ai.memory import _memory_db

    async with await _memory_db("skills", group_id, write=False) as db:
        async with db.execute(
            """SELECT skill_id,bot_id,name,maturity,status,current_version
               FROM skills WHERE group_id=?""",
            (group_id,),
        ) as cur:
            rows = await cur.fetchall()
    missing = [
        row
        for row in rows
        if not _projection_matches(
            skill_id=str(row[0]),
            bot_id=int(row[1]),
            group_id=group_id,
            name=str(row[2]),
            maturity=str(row[3]),
            status=str(row[4]),
            version=int(row[5]),
        )
    ]
    if not missing:
        return 0
    async with await _memory_db("pipeline_jobs", group_id, write=True) as db:
        for skill_id, _bot_id, _name, maturity, status, version in missing:
            await _enqueue_skill_projection(
                db,
                skill_id=str(skill_id),
                group_id=group_id,
                version=int(version),
                maturity=str(maturity),
                status=str(status),
                reopen_completed=True,
            )
        await db.commit()
    return len(missing)
