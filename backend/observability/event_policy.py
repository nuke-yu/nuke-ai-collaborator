"""Executable policy for business-significant Agent execution events.

The model never decides whether an event is auditable.  Platform code resolves
that from the event type plus deterministic tool-effect classification.  The
result is stored with the existing session event so recovery remains backward
compatible while Timeline/Audit consumers gain a stable routing contract.

This module deliberately classifies and annotates only.  Payload redaction,
retention enforcement, and export to an external tracing backend remain the
responsibility of their respective storage/transport boundaries.
"""

from __future__ import annotations

import re
import shlex
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


EVENT_POLICY_VERSION = 1
OBSERVABILITY_KEY = "_observability"


class EventClass(StrEnum):
    AUDIT = "audit"
    TIMELINE = "timeline"
    DIAGNOSTIC = "diagnostic"
    METRIC = "metric"
    EPHEMERAL = "ephemeral"


class EffectClass(StrEnum):
    NONE = "none"
    READ = "read"
    DURABLE_WRITE = "durable_write"
    EXTERNAL_WRITE = "external_write"
    AUTHORIZATION = "authorization"
    CONTROL_FLOW = "control_flow"
    RECOVERY = "recovery"
    BILLABLE = "billable"
    LEARNING = "learning"
    VERIFICATION = "verification"
    LIFECYCLE = "lifecycle"
    UNKNOWN = "unknown"


class RetentionPolicy(StrEnum):
    STREAM_LIFETIME = "stream_lifetime"
    DIAGNOSTIC_14_DAYS = "diagnostic_14_days"
    EXECUTION_90_DAYS = "execution_90_days"
    GROUP_LIFETIME = "group_lifetime"
    SECURITY_AUDIT = "security_audit"


class PayloadPolicy(StrEnum):
    REDACTED = "redacted"
    SUMMARY = "summary"
    REFERENCE_ONLY = "reference_only"


@dataclass(frozen=True)
class EventPolicy:
    event_classes: tuple[EventClass, ...]
    effect_classes: tuple[EffectClass, ...]
    retention: RetentionPolicy
    payload_policy: PayloadPolicy
    business_significant: bool
    allow_sampling: bool
    reason: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "policy_version": EVENT_POLICY_VERSION,
            "event_id": f"evt_{uuid.uuid4().hex}",
            "classes": [item.value for item in self.event_classes],
            "effects": [item.value for item in self.effect_classes],
            "retention": self.retention.value,
            "payload_policy": self.payload_policy.value,
            "business_significant": self.business_significant,
            "allow_sampling": self.allow_sampling,
            "reason": self.reason,
        }


def _policy(
    classes: tuple[EventClass, ...],
    effects: tuple[EffectClass, ...],
    retention: RetentionPolicy,
    payload: PayloadPolicy,
    significant: bool,
    sampling: bool,
    reason: str,
) -> EventPolicy:
    return EventPolicy(classes, effects, retention, payload, significant, sampling, reason)


_EVENT_POLICIES: dict[str, EventPolicy] = {
    "session_start": _policy(
        (EventClass.TIMELINE,), (EffectClass.LIFECYCLE,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.REDACTED,
        True, False, "starts a recoverable Bot execution",
    ),
    "llm_response": _policy(
        (EventClass.TIMELINE, EventClass.METRIC), (EffectClass.BILLABLE,),
        RetentionPolicy.EXECUTION_90_DAYS, PayloadPolicy.SUMMARY,
        True, False, "consumes billable model capacity and advances execution",
    ),
    "child_fork": _policy(
        (EventClass.TIMELINE,), (EffectClass.CONTROL_FLOW, EffectClass.LIFECYCLE),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "creates a delegated child execution",
    ),
    "child_join": _policy(
        (EventClass.TIMELINE,), (EffectClass.CONTROL_FLOW, EffectClass.LIFECYCLE),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "joins delegated work back into the parent execution",
    ),
    "context_evidence_injected": _policy(
        (EventClass.TIMELINE,), (EffectClass.LEARNING,),
        RetentionPolicy.EXECUTION_90_DAYS, PayloadPolicy.SUMMARY,
        True, False,
        "records Memory/Skill context available to a run without claiming causal adoption",
    ),
    "permission_requested": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.AUTHORIZATION,),
        RetentionPolicy.SECURITY_AUDIT, PayloadPolicy.REDACTED,
        True, False, "crosses a human authorization boundary",
    ),
    "permission_approved": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.AUTHORIZATION,),
        RetentionPolicy.SECURITY_AUDIT, PayloadPolicy.REDACTED,
        True, False, "grants authority for a protected operation",
    ),
    "permission_denied": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.AUTHORIZATION,),
        RetentionPolicy.SECURITY_AUDIT, PayloadPolicy.REDACTED,
        True, False, "denies authority for a protected operation",
    ),
    "session_recovered": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.RECOVERY,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "changes retry and side-effect safety after interruption",
    ),
    "session_failed": _policy(
        (EventClass.TIMELINE, EventClass.METRIC), (EffectClass.LIFECYCLE,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "terminates a recoverable execution unsuccessfully",
    ),
    "session_completed": _policy(
        (EventClass.TIMELINE, EventClass.METRIC), (EffectClass.LIFECYCLE,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "terminates a recoverable execution successfully",
    ),
    "workflow_started": _policy(
        (EventClass.TIMELINE,), (EffectClass.LIFECYCLE, EffectClass.CONTROL_FLOW),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "starts a durable multi-agent workflow",
    ),
    "stage_entered": _policy(
        (EventClass.TIMELINE,), (EffectClass.CONTROL_FLOW,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "assigns responsibility for the active workflow stage",
    ),
    "stage_completed": _policy(
        (EventClass.TIMELINE, EventClass.METRIC),
        (EffectClass.CONTROL_FLOW, EffectClass.VERIFICATION),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "records accepted stage completion evidence",
    ),
    "gate_requested": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.AUTHORIZATION,),
        RetentionPolicy.SECURITY_AUDIT, PayloadPolicy.SUMMARY,
        True, False, "workflow progression is suspended for human authorization",
    ),
    "gate_approved": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE),
        (EffectClass.AUTHORIZATION, EffectClass.CONTROL_FLOW),
        RetentionPolicy.SECURITY_AUDIT, PayloadPolicy.SUMMARY,
        True, False, "human authorization changes workflow control flow",
    ),
    "gate_revision_requested": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE),
        (EffectClass.AUTHORIZATION, EffectClass.CONTROL_FLOW),
        RetentionPolicy.SECURITY_AUDIT, PayloadPolicy.SUMMARY,
        True, False, "human review rejects the current output and requests revision",
    ),
    "stage_rework_started": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE),
        (EffectClass.RECOVERY, EffectClass.CONTROL_FLOW),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "rewinds responsibility to an earlier workflow stage",
    ),
    "workflow_paused": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.CONTROL_FLOW,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "suspends autonomous workflow progression",
    ),
    "workflow_completed": _policy(
        (EventClass.TIMELINE, EventClass.METRIC), (EffectClass.LIFECYCLE,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "terminates a workflow successfully",
    ),
    "workflow_failed": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE, EventClass.METRIC),
        (EffectClass.LIFECYCLE,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "terminates a workflow unsuccessfully",
    ),
    "workflow_recovered": _policy(
        (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.RECOVERY,),
        RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
        True, False, "restores a durable workflow after process interruption",
    ),
}


_READ_TOOLS = frozenset({
    "read_file", "list_files", "search_files",
    "web_search", "memory_search", "search_tool_events", "timeline_tool_events",
    "fetch_tool_events", "get_current_status", "list_skills", "list_agents",
})
_WRITE_TOOLS = frozenset({
    "write_file", "write_local_file", "edit_file", "apply_patch", "append_log",
    "create_file", "delete_file", "move_file", "copy_file",
})
_EXTERNAL_TOOLS = frozenset({
    "create_pr", "create_pull_request", "send_message", "send_email",
    "create_jira_ticket", "update_jira_ticket", "notify", "deploy",
})
_CONTROL_TOOLS = frozenset({
    "spawn_agent", "signal_stage_done", "signal_rework", "handoff",
    "start_workflow", "stop", "abort",
})
_LEARNING_TOOLS = frozenset({
    "write_memory", "forget_memory", "promote_skill", "retire_skill",
    "create_experience", "record_reflection",
})
_SHELL_TOOLS = frozenset({"run_shell", "bash", "shell"})
_SENSITIVE_READ_TOOLS = frozenset({"read_local_file"})
_SENSITIVE_PATH_PARTS = frozenset({
    ".aws", ".env", ".gnupg", ".ssh", "auth.json", "credentials",
    "credentials.json", "id_dsa", "id_ed25519", "id_rsa", "secrets",
    "secrets.json", "tokens.json",
})

_READ_COMMANDS = frozenset({
    "cat", "cut", "du", "find", "git", "grep", "head", "ls",
    "pwd", "rg", "sed", "stat", "tail", "tree", "wc", "which",
})
_GIT_READ_SUBCOMMANDS = frozenset({"branch", "diff", "log", "show", "status"})
_VERIFICATION_COMMANDS = frozenset({"pytest", "ruff", "mypy", "eslint", "vitest"})
_VERIFICATION_SCRIPTS = frozenset({"build", "check", "lint", "test", "typecheck"})
_EXTERNAL_COMMAND_PAIRS = frozenset({
    ("git", "push"), ("gh", "pr"), ("gh", "issue"),
    ("docker", "push"), ("npm", "publish"), ("kubectl", "apply"),
    ("kubectl", "delete"), ("terraform", "apply"),
})
_WRITE_COMMANDS = frozenset({
    "chmod", "chown", "cp", "install", "ln", "mkdir", "mv", "rm",
    "rmdir", "tee", "touch", "truncate",
})
_WRAPPERS = frozenset({"command", "doas", "env", "nice", "nohup", "sudo", "time", "timeout"})


def _shell_tokens(arguments: Mapping[str, Any]) -> list[str]:
    command = str(arguments.get("cmd") or arguments.get("command") or "").strip()
    if not command:
        return []
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.match(r"^[A-Za-z_]\w*=", token):
            index += 1
            continue
        if token.split("/")[-1] in _WRAPPERS:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        break
    return tokens[index:]


def _contains_sensitive_path(values: Any) -> bool:
    if isinstance(values, Mapping):
        return any(_contains_sensitive_path(value) for value in values.values())
    if isinstance(values, (list, tuple, set)):
        return any(_contains_sensitive_path(value) for value in values)
    if not isinstance(values, str):
        return False
    normalized = values.replace("\\", "/").lower()
    parts = {part for part in normalized.split("/") if part}
    return any(
        part in _SENSITIVE_PATH_PARTS or part.startswith(".env.")
        for part in parts
    )


def _tool_policy(
    *,
    classes: tuple[EventClass, ...],
    effects: tuple[EffectClass, ...],
    retention: RetentionPolicy,
    payload: PayloadPolicy,
    significant: bool,
    sampling: bool,
    reason: str,
) -> EventPolicy:
    return _policy(classes, effects, retention, payload, significant, sampling, reason)


def _classify_shell(arguments: Mapping[str, Any]) -> EventPolicy:
    raw_command = str(arguments.get("cmd") or arguments.get("command") or "").strip()
    tokens = _shell_tokens(arguments)
    if not tokens:
        return _tool_policy(
            classes=(EventClass.AUDIT,), effects=(EffectClass.UNKNOWN,),
            retention=RetentionPolicy.SECURITY_AUDIT, payload=PayloadPolicy.REDACTED,
            significant=True, sampling=False,
            reason="empty or unparsable shell command is classified conservatively",
        )

    base = tokens[0].split("/")[-1]
    subcommand = tokens[1] if len(tokens) > 1 else ""
    pair = (base, subcommand)

    # A compound/redirection command cannot be classified safely from only its
    # first executable.  Keep the common single-command fast path precise and
    # audit compound shell programs conservatively.
    if re.search(r"(?:&&|\|\||[;|]|>>?|<)", raw_command):
        return _tool_policy(
            classes=(EventClass.AUDIT,), effects=(EffectClass.UNKNOWN,),
            retention=RetentionPolicy.SECURITY_AUDIT, payload=PayloadPolicy.REDACTED,
            significant=True, sampling=False,
            reason="compound or redirected shell command is audited conservatively",
        )

    if pair in _EXTERNAL_COMMAND_PAIRS or base in {"ssh", "scp", "rsync"}:
        return _tool_policy(
            classes=(EventClass.AUDIT, EventClass.TIMELINE),
            effects=(EffectClass.EXTERNAL_WRITE,),
            retention=RetentionPolicy.SECURITY_AUDIT, payload=PayloadPolicy.REDACTED,
            significant=True, sampling=False,
            reason="shell command can change an external system",
        )
    package_script = (
        base in {"npm", "pnpm", "yarn"}
        and subcommand == "run"
        and len(tokens) > 2
        and tokens[2] in _VERIFICATION_SCRIPTS
    )
    python_test = (
        (base == "python" or re.fullmatch(r"python\d+(?:\.\d+)*", base) is not None)
        and len(tokens) > 2
        and tokens[1] == "-m"
        and tokens[2] in {"pytest", "unittest"}
    )
    if base in _VERIFICATION_COMMANDS or package_script or python_test or pair in {
        ("npm", "test"), ("pnpm", "test"),
        ("yarn", "test"), ("cargo", "test"), ("cargo", "check"),
        ("go", "test"), ("make", "test"),
    }:
        return _tool_policy(
            classes=(EventClass.TIMELINE, EventClass.METRIC),
            effects=(EffectClass.VERIFICATION,),
            retention=RetentionPolicy.GROUP_LIFETIME, payload=PayloadPolicy.SUMMARY,
            significant=True, sampling=False,
            reason="shell result can serve as execution verification evidence",
        )
    if base == "git" and subcommand in _GIT_READ_SUBCOMMANDS:
        if subcommand == "branch" and len(tokens) > 2 and not any(
            flag in {"-a", "--all", "-l", "--list", "-r", "--remotes", "-v", "-vv"}
            for flag in tokens[2:]
        ):
            return _tool_policy(
                classes=(EventClass.AUDIT, EventClass.TIMELINE),
                effects=(EffectClass.DURABLE_WRITE,),
                retention=RetentionPolicy.GROUP_LIFETIME,
                payload=PayloadPolicy.REDACTED,
                significant=True,
                sampling=False,
                reason="git branch command can change durable repository state",
            )
        return _tool_policy(
            classes=(EventClass.DIAGNOSTIC,), effects=(EffectClass.READ,),
            retention=RetentionPolicy.DIAGNOSTIC_14_DAYS, payload=PayloadPolicy.SUMMARY,
            significant=False, sampling=True,
            reason="read-only git inspection does not change project reality",
        )
    if base in _READ_COMMANDS and base != "git":
        if (base == "sed" and any(token == "-i" or token.startswith("-i") for token in tokens[1:])) \
                or (base == "find" and "-delete" in tokens[1:]):
            return _tool_policy(
                classes=(EventClass.AUDIT, EventClass.TIMELINE),
                effects=(EffectClass.DURABLE_WRITE,),
                retention=RetentionPolicy.GROUP_LIFETIME,
                payload=PayloadPolicy.REDACTED,
                significant=True,
                sampling=False,
                reason="nominally read-oriented command includes a write option",
            )
        if _contains_sensitive_path(tokens[1:]):
            return _tool_policy(
                classes=(EventClass.AUDIT,), effects=(EffectClass.READ,),
                retention=RetentionPolicy.SECURITY_AUDIT,
                payload=PayloadPolicy.REDACTED,
                significant=True,
                sampling=False,
                reason="shell command reads a sensitive credential or secret path",
            )
        return _tool_policy(
            classes=(EventClass.DIAGNOSTIC,), effects=(EffectClass.READ,),
            retention=RetentionPolicy.DIAGNOSTIC_14_DAYS, payload=PayloadPolicy.SUMMARY,
            significant=False, sampling=True,
            reason="known read-only shell inspection",
        )
    if base in _WRITE_COMMANDS or (base == "git" and subcommand):
        return _tool_policy(
            classes=(EventClass.AUDIT, EventClass.TIMELINE),
            effects=(EffectClass.DURABLE_WRITE,),
            retention=RetentionPolicy.GROUP_LIFETIME, payload=PayloadPolicy.REDACTED,
            significant=True, sampling=False,
            reason="shell command can change durable project state",
        )
    return _tool_policy(
        classes=(EventClass.AUDIT,), effects=(EffectClass.UNKNOWN,),
        retention=RetentionPolicy.SECURITY_AUDIT, payload=PayloadPolicy.REDACTED,
        significant=True, sampling=False,
        reason="unknown shell effect is audited conservatively",
    )


def classify_tool_effect(tool_name: str, arguments: Mapping[str, Any] | None = None) -> EventPolicy:
    """Classify a concrete tool invocation from deterministic name + arguments."""
    name = str(tool_name or "").strip()
    args = arguments if isinstance(arguments, Mapping) else {}

    if name in _SHELL_TOOLS:
        return _classify_shell(args)
    if name in _SENSITIVE_READ_TOOLS or (
        name in _READ_TOOLS and _contains_sensitive_path(args)
    ):
        return _tool_policy(
            classes=(EventClass.AUDIT,), effects=(EffectClass.READ,),
            retention=RetentionPolicy.SECURITY_AUDIT,
            payload=PayloadPolicy.REDACTED,
            significant=True,
            sampling=False,
            reason="tool reads outside the workspace or accesses a sensitive path",
        )
    if name in _READ_TOOLS:
        return _tool_policy(
            classes=(EventClass.DIAGNOSTIC,), effects=(EffectClass.READ,),
            retention=RetentionPolicy.DIAGNOSTIC_14_DAYS, payload=PayloadPolicy.SUMMARY,
            significant=False, sampling=True,
            reason="known read-only tool",
        )
    if name in _WRITE_TOOLS:
        return _tool_policy(
            classes=(EventClass.AUDIT, EventClass.TIMELINE),
            effects=(EffectClass.DURABLE_WRITE,),
            retention=RetentionPolicy.GROUP_LIFETIME, payload=PayloadPolicy.REDACTED,
            significant=True, sampling=False,
            reason="tool changes durable workspace state",
        )
    if name in _EXTERNAL_TOOLS:
        return _tool_policy(
            classes=(EventClass.AUDIT, EventClass.TIMELINE),
            effects=(EffectClass.EXTERNAL_WRITE,),
            retention=RetentionPolicy.SECURITY_AUDIT, payload=PayloadPolicy.REDACTED,
            significant=True, sampling=False,
            reason="tool changes an external system or creates an external commitment",
        )
    if name in _CONTROL_TOOLS:
        return _tool_policy(
            classes=(EventClass.TIMELINE,), effects=(EffectClass.CONTROL_FLOW,),
            retention=RetentionPolicy.GROUP_LIFETIME, payload=PayloadPolicy.SUMMARY,
            significant=True, sampling=False,
            reason="tool changes delegation or workflow control flow",
        )
    if name in _LEARNING_TOOLS:
        return _tool_policy(
            classes=(EventClass.AUDIT, EventClass.TIMELINE),
            effects=(EffectClass.LEARNING,),
            retention=RetentionPolicy.GROUP_LIFETIME,
            payload=PayloadPolicy.REDACTED,
            significant=True,
            sampling=False,
            reason="tool changes durable memory or learned capability",
        )
    if name == "run_skill":
        return _tool_policy(
            classes=(EventClass.TIMELINE,), effects=(EffectClass.CONTROL_FLOW,),
            retention=RetentionPolicy.EXECUTION_90_DAYS, payload=PayloadPolicy.SUMMARY,
            significant=True, sampling=False,
            reason="skill adoption changes the execution path and learning evidence",
        )
    return _tool_policy(
        classes=(EventClass.AUDIT,), effects=(EffectClass.UNKNOWN,),
        retention=RetentionPolicy.SECURITY_AUDIT, payload=PayloadPolicy.REDACTED,
        significant=True, sampling=False,
        reason="unknown or plugin tool is audited conservatively",
    )


def classify_event(event_type: str, payload: Mapping[str, Any] | None = None) -> EventPolicy:
    """Resolve the policy for one concrete session event."""
    kind = str(event_type or "").strip()
    data = payload if isinstance(payload, Mapping) else {}
    if kind in {"tool_call", "tool_result"}:
        return classify_tool_effect(
            str(data.get("tool_name") or data.get("tool") or ""),
            data.get("arguments") if isinstance(data.get("arguments"), Mapping) else {},
        )
    if kind == "session_status":
        status = str(data.get("status") or "").strip()
        if status in {"needs_review", "awaiting_recovery", "recovering"}:
            return _policy(
                (EventClass.AUDIT, EventClass.TIMELINE), (EffectClass.RECOVERY,),
                RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
                True, False, "session status changes recovery or retry safety",
            )
        if status in {"completed", "failed"}:
            return _policy(
                (EventClass.TIMELINE, EventClass.METRIC), (EffectClass.LIFECYCLE,),
                RetentionPolicy.GROUP_LIFETIME, PayloadPolicy.SUMMARY,
                True, False, "session reaches a terminal lifecycle state",
            )
        return _policy(
            (EventClass.DIAGNOSTIC,), (EffectClass.LIFECYCLE,),
            RetentionPolicy.EXECUTION_90_DAYS, PayloadPolicy.SUMMARY,
            False, True, "non-terminal session lifecycle transition",
        )
    policy = _EVENT_POLICIES.get(kind)
    if policy is not None:
        return policy
    return _policy(
        (EventClass.DIAGNOSTIC,), (EffectClass.UNKNOWN,),
        RetentionPolicy.DIAGNOSTIC_14_DAYS, PayloadPolicy.SUMMARY,
        False, True, "unregistered event defaults to sampled diagnostic retention",
    )


def enrich_event_payload(
    event_type: str,
    payload: Mapping[str, Any] | None,
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Return a copy carrying immutable v1 observability policy metadata.

    Existing metadata is preserved to keep replay/import idempotent.  Callers
    retain their original payload object and recovery readers may ignore the
    reserved ``_observability`` key.
    """
    enriched = dict(payload or {})
    existing = enriched.get(OBSERVABILITY_KEY)
    if isinstance(existing, Mapping):
        if trace_id and not existing.get("trace_id"):
            enriched[OBSERVABILITY_KEY] = {**existing, "trace_id": trace_id}
        return enriched
    metadata = classify_event(event_type, enriched).to_metadata()
    if trace_id:
        metadata["trace_id"] = trace_id
    enriched[OBSERVABILITY_KEY] = metadata
    return enriched
