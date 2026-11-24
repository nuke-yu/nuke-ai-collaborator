"""Session start, resume, and execution-trace coordination for conversations."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import aiosqlite

logger = logging.getLogger(__name__)


class SessionInteractionPort(Protocol):
    """Session operations required by the conversation application service."""

    async def update_session_status(self, session_id: str, status: str) -> None: ...
    async def create_session(self, **kwargs: Any) -> Any: ...
    async def append_session_event(
        self, session_id: str, event_type: str, payload: dict
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class SessionStartRequest:
    """Explicit inputs for creating or resuming one persisted Agent Session."""

    interaction: SessionInteractionPort
    session_id: str
    group_id: int
    bot_id: int
    user_message: str
    session_config: Mapping[str, Any]
    executor_id: str
    executor_version: str
    provider: str
    model: str
    system_prompt: str
    bot: Mapping[str, Any]
    tool_schemas: Sequence[dict]
    skills: Sequence[dict]
    ruleset: Any
    permission_mode: str
    memory_refs: Sequence[str]
    always_skills: Sequence[dict]
    user_content: Any
    resuming: bool
    thread_id: str | None
    run_id: str


@dataclass(frozen=True, slots=True)
class SessionStartResult:
    """Stable Session facts produced during conversation setup."""

    capability_manifest: dict[str, Any] | None
    manifest_hash: str | None
    memory_injection_decision_id: str | None


def context_evidence_links(memory_refs: list[str], always_skills: list[dict]) -> list[dict]:
    """Describe recalled evidence availability separately from later citation."""
    from sessions.evidence import evidence_kind

    links = [
        {
            "kind": evidence_kind(ref),
            "ref": ref,
            "relation": "injected",
            "metadata": {"source": "learned_context_recall"},
        }
        for ref in memory_refs
    ]
    links.extend(
        skill["evidence_link"]
        for skill in always_skills
        if isinstance(skill.get("evidence_link"), dict)
    )
    return links


async def start_session(request: SessionStartRequest) -> SessionStartResult:
    """Persist one Session boundary and its execution trace through public ports."""
    capability_manifest = None
    manifest_hash = None
    memory_injection_decision_id = None
    if request.resuming:
        await request.interaction.update_session_status(request.session_id, "running")
    else:
        from sessions.manifest import build_capability_manifest

        capability_manifest, manifest_hash = build_capability_manifest(
            provider=request.provider,
            model=request.model,
            executor_id=request.executor_id,
            executor_version=request.executor_version,
            system_prompt=request.system_prompt,
            bot=dict(request.bot),
            tool_schemas=list(request.tool_schemas),
            skills=list(request.skills),
            permission_rules=request.ruleset,
            sandbox_policy={
                "permission_mode": request.permission_mode
            },
            memory_revision=";".join(sorted(str(ref) for ref in request.memory_refs)),
        )
        await request.interaction.create_session(
            session_id=request.session_id,
            bot_id=request.bot_id,
            group_id=request.group_id,
            config=dict(request.session_config),
            user_message=request.user_message,
            executor_id=request.executor_id,
            manifest=capability_manifest,
            manifest_hash=manifest_hash,
            manifest_version=capability_manifest["manifest_version"],
        )
        await request.interaction.append_session_event(
            request.session_id,
            "session_start",
            {
                "user_content": (
                    request.user_content
                    if isinstance(request.user_content, str)
                    else json.dumps(request.user_content, ensure_ascii=False)
                ),
                "manifest_hash": manifest_hash,
            },
        )

    evidence = context_evidence_links(
        list(request.memory_refs), list(request.always_skills)
    )
    if evidence:
        await request.interaction.append_session_event(
            request.session_id,
            "context_evidence_injected",
            {
                "evidence_links": evidence,
                "reference_count": len(evidence),
                "causal_usage": False,
                "recovery_resume": request.resuming,
            },
        )

    from memory.application.execution_runs import start_run

    try:
        await start_run(
            run_id=request.run_id,
            group_id=request.group_id,
            bot_id=request.bot_id,
            session_id=request.session_id,
            thread_id=request.thread_id,
            provider=request.provider,
            model=request.model,
            executor=request.executor_id,
        )
        from memory.application.reflexion_service import record_memory_injection

        memory_injection_decision_id = await record_memory_injection(
            run_id=request.run_id,
            group_id=request.group_id,
            bot_id=request.bot_id,
            memory_refs=list(request.memory_refs),
        )
    except aiosqlite.OperationalError:
        logger.warning("run trace unavailable; group schema is not ready", exc_info=True)
    return SessionStartResult(
        capability_manifest=capability_manifest,
        manifest_hash=manifest_hash,
        memory_injection_decision_id=memory_injection_decision_id,
    )


async def persist_session_start(runner, *, user_content, resuming: bool, thread_id: str) -> None:
    """Compatibility adapter from the current executor state to SessionStartRequest."""
    executor_version = getattr(runner.executor.manifest, "version", "1")
    if not isinstance(executor_version, str):
        executor_version = "1"
    permission_mode = runner.ruleset.mode if runner.ruleset else "default"
    result = await start_session(SessionStartRequest(
        interaction=runner.ctx.interaction,
        session_id=runner.session_id,
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        user_message=runner.ctx.user_message,
        session_config={
            "system_prompt": runner.system_prompt,
            "provider": runner.provider,
            "model_name": runner.model_name,
            "temperature": runner.temperature,
            "max_tokens": runner.max_tokens,
        },
        executor_id=runner.executor.executor_id,
        executor_version=executor_version,
        provider=runner.provider,
        model=runner.model_name,
        system_prompt=runner.system_prompt,
        bot=runner.bot,
        tool_schemas=runner.tool_schemas,
        skills=runner.skills_snapshot,
        ruleset=runner.ruleset,
        permission_mode=permission_mode,
        memory_refs=runner.injected_memory_refs,
        always_skills=runner.always_skills,
        user_content=user_content,
        resuming=resuming,
        thread_id=thread_id,
        run_id=getattr(runner, "run_id", runner.session_id),
    ))
    runner.capability_manifest = result.capability_manifest
    runner.manifest_hash = result.manifest_hash
    runner.memory_injection_decision_id = result.memory_injection_decision_id
