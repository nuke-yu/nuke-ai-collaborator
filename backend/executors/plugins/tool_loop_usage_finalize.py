"""Evidence-bearing Memory/Skill usage finalization."""
from __future__ import annotations

from memory.contracts import MarkUsageAdopted, MarkUsageExecuted, VerifyUsage


async def finalize_causal_memory_usage(runner, *, scope, learning_port) -> None:
    from memory.application.reflexion_service import record_memory_adoption
    from memory.application.causal_usage import collect_causal_usages, verification_for_usage
    usages = collect_causal_usages(runner.tool_records, getattr(runner, "injected_memory_refs", ()))
    if not usages:
        return
    decision_id = await record_memory_adoption(
        run_id=runner.run_id, group_id=runner.ctx.group_id, bot_id=runner.bot["id"],
        evidence_by_ref={u.memory_ref: u.action_evidence_ids for u in usages},
    )
    if decision_id is None:
        return
    for usage in usages:
        item_ids = (usage.item_id,)
        await learning_port.mark_usage_adopted(MarkUsageAdopted(
            scope=scope, kind=usage.kind, item_ids=item_ids, run_id=runner.run_id,
            adopted_via="decision_trace", evidence={"decision_id": decision_id, "memory_ref": usage.memory_ref},
        ))
        await learning_port.mark_usage_executed(MarkUsageExecuted(
            scope=scope, kind=usage.kind, item_ids=item_ids, run_id=runner.run_id,
            evidence={"action_match": True, "evidence_ids": list(usage.action_evidence_ids), "memory_ref": usage.memory_ref},
        ))
        verification = verification_for_usage(usage, runner.tool_records, terminal_outcome="completed")
        if verification is not None:
            status, evidence = verification
            await learning_port.verify_usage(VerifyUsage(
                scope=scope, kind=usage.kind, item_ids=item_ids, run_id=runner.run_id,
                status=status, evidence=evidence,
            ))
