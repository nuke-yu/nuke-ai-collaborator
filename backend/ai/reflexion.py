"""Bounded execution Reflexion using structured traces, never raw chain-of-thought."""
from __future__ import annotations
import hashlib
import time


def classify_failure(tool: str, result: str) -> str:
    text = (result or "").lower()
    if any(x in text for x in ("permission", "denied", "approval")):
        return "permission_blocked"
    if any(x in text for x in ("timeout", "timed out", "temporarily")):
        return "transient"
    if tool in {"run_shell", "write_file", "edit_file"}:
        return "correctable_execution"
    return "unknown"


def corrective_prompt(failure_class: str, tool: str, result: str) -> str:
    return ("[Execution Reflexion]\n"
            f"Failure class: {failure_class}\nFailed tool: {tool}\n"
            f"Observation: {(result or '')[:600]}\n"
            "Re-check assumptions and choose one corrected next action. Do not repeat the identical "
            "call without a concrete change. All normal permissions and tool guards still apply.")


async def record(*, run_id: str, group_id: int | None, bot_id: int | None,
                 step_id: str, failure_class: str, observation: str,
                 corrective_plan: str) -> None:
    if group_id is None:
        return
    from ai.memory import _memory_db
    decision_id = "decision:" + hashlib.sha256(f"{run_id}:{step_id}:reflexion".encode()).hexdigest()[:24]
    async with await _memory_db("run_decisions", group_id, write=True) as db:
        await db.execute("""INSERT INTO run_decisions
          (decision_id,run_id,group_id,bot_id,step_id,decision_type,failure_class,
           observation,corrective_plan,created_at) VALUES (?,?,?,?,?,'reflexion',?,?,?,?)
          ON CONFLICT(run_id,step_id,decision_type) DO NOTHING""",
          (decision_id,run_id,group_id,bot_id,step_id,failure_class,
           observation[:1000],corrective_plan[:1000],int(time.time()*1000)))
        await db.commit()


async def maybe_inject(runner, *, iteration: int) -> bool:
    if getattr(runner, "reflexion_used", False):
        return False
    failed = next((r for r in reversed(runner.tool_records) if r.get("is_error")), None)
    if not failed:
        return False
    failure_class = classify_failure(failed.get("name", ""), failed.get("result", ""))
    if failure_class in {"permission_blocked", "unknown"}:
        return False
    prompt = corrective_prompt(failure_class, failed.get("name", ""), failed.get("result", ""))
    runner.messages.append({"role":"user","content":prompt})
    runner.reflexion_used = True
    await record(run_id=runner.run_id,group_id=runner.ctx.group_id,bot_id=runner.bot["id"],
                 step_id=f"{runner.run_id}:step:{iteration}",failure_class=failure_class,
                 observation=failed.get("result", ""),corrective_plan="replan_once")
    return True
