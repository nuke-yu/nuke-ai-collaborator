"""Persistence boundary for tool-loop session startup and execution traces."""
from __future__ import annotations

import json
import logging

import aiosqlite

from executors.plugins.tool_loop_provenance import context_evidence_links

logger = logging.getLogger(__name__)


async def persist_session_start(runner, *, user_content, resuming: bool, thread_id: str) -> None:
    """Persist session identity, evidence links, and the execution trace.

    This function deliberately owns only durable startup side effects. Prompt
    construction and transport broadcasts remain in the tool-loop coordinator.
    """
    if resuming:
        await runner.ctx.interaction.update_session_status(runner.session_id, "running")
    else:
        session_config = {
            "system_prompt": runner.system_prompt,
            "provider": runner.provider,
            "model_name": runner.model_name,
            "temperature": runner.temperature,
            "max_tokens": runner.max_tokens,
        }
        from sessions.manifest import build_capability_manifest

        executor_version = getattr(runner.executor.manifest, "version", "1")
        if not isinstance(executor_version, str):
            executor_version = "1"
        runner.capability_manifest, runner.manifest_hash = build_capability_manifest(
            provider=runner.provider,
            model=runner.model_name,
            executor_id=runner.executor.executor_id,
            executor_version=executor_version,
            system_prompt=runner.system_prompt,
            bot=runner.bot,
            tool_schemas=runner.tool_schemas,
            skills=runner.skills_snapshot,
            permission_rules=runner.ruleset,
            sandbox_policy={
                "permission_mode": runner.ruleset.mode if runner.ruleset else "default"
            },
            memory_revision=";".join(sorted(str(ref) for ref in runner.injected_memory_refs)),
        )
        await runner.ctx.interaction.create_session(
            session_id=runner.session_id,
            bot_id=runner.bot["id"],
            group_id=runner.ctx.group_id,
            config=session_config,
            user_message=runner.ctx.user_message,
            executor_id=runner.executor.executor_id,
            manifest=runner.capability_manifest,
            manifest_hash=runner.manifest_hash,
            manifest_version=runner.capability_manifest["manifest_version"],
        )
        await runner.ctx.interaction.append_session_event(
            runner.session_id,
            "session_start",
            {
                "user_content": (
                    user_content
                    if isinstance(user_content, str)
                    else json.dumps(user_content, ensure_ascii=False)
                ),
                "manifest_hash": runner.manifest_hash,
            },
        )

    evidence = context_evidence_links(
        runner.injected_memory_refs, runner.always_skills
    )
    if evidence:
        await runner.ctx.interaction.append_session_event(
            runner.session_id,
            "context_evidence_injected",
            {
                "evidence_links": evidence,
                "reference_count": len(evidence),
                "causal_usage": False,
                "recovery_resume": resuming,
            },
        )

    from memory.application.execution_runs import start_run

    try:
        await start_run(
            run_id=getattr(runner, "run_id", runner.session_id),
            group_id=runner.ctx.group_id,
            bot_id=runner.bot["id"],
            session_id=runner.session_id,
            thread_id=thread_id,
            provider=runner.provider,
            model=runner.model_name,
            executor=runner.executor.executor_id,
        )
        from memory.application.reflexion_service import record_memory_injection

        runner.memory_injection_decision_id = await record_memory_injection(
            run_id=getattr(runner, "run_id", runner.session_id),
            group_id=runner.ctx.group_id,
            bot_id=runner.bot["id"],
            memory_refs=runner.injected_memory_refs,
        )
    except aiosqlite.OperationalError:
        logger.warning("run trace unavailable; group schema is not ready", exc_info=True)
