"""Validation and repository resolution for Dashboard-supplied Story data."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from executors.redaction import redact_secrets

_JIRA_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SERVICE_SUMMARY = re.compile(r"^\[([A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?)\](?:\s+.*)?$")
_GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")


def clean_text(value: str, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Story fields must be strings")
    if len(value) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    redacted, _ = redact_secrets(value.strip())
    if len(redacted) > limit:
        raise ValueError(f"{field} exceeds {limit} characters after redaction")
    return redacted


def repository_mapping_from_summary(summary: str, owner: str = "nuke-yu") -> dict | None:
    """Resolve a leading [service-name] marker to its same-named GitHub repo."""
    if not _GITHUB_OWNER.fullmatch(owner):
        raise ValueError("GitHub owner is invalid")
    match = _SERVICE_SUMMARY.fullmatch(summary.strip())
    if not match:
        return None
    service = match.group(1)
    return {
        "repo_url": f"https://github.com/{owner}/{service}.git",
        "base_branch": "main",
        "test_command": "",
    }


def build_story_input(
    jira_id: str,
    summary: str,
    description: str,
    acceptance_criteria: str,
    *,
    github_owner: str = "nuke-yu",
) -> dict:
    if not isinstance(jira_id, str) or not _JIRA_ID.fullmatch(jira_id.strip()):
        raise ValueError("jira_id must be a non-empty Jira issue ID or key")
    clean_id = jira_id.strip()
    clean_summary = clean_text(summary, 500, "summary")
    clean_description = clean_text(description, 12000, "description")
    clean_acceptance = clean_text(acceptance_criteria, 8000, "acceptance_criteria")
    if not any((clean_summary, clean_description, clean_acceptance)):
        raise ValueError("At least one Story content field must be provided")

    fields = (
        ("Jira ID", clean_id),
        ("Summary", clean_summary),
        ("Description", clean_description),
        ("Acceptance criteria", clean_acceptance),
    )
    requirements = "\n\n".join(f"{label}:\n{value}" for label, value in fields)
    return {
        "jira_id": clean_id,
        "summary": clean_summary,
        "description": clean_description,
        "acceptance_criteria": clean_acceptance,
        "requirements": requirements,
        "mapping": repository_mapping_from_summary(clean_summary, github_owner),
    }


def task_scope() -> str:
    """Build a stable task namespace; accept legacy site configuration."""
    configured = (os.getenv("NUKE_ONLINE_TASK_SCOPE") or os.getenv("NUKE_JIRA_SITE")
                  or os.getenv("NUKE_JIRA_URL") or "default").strip()
    if "\n" in configured or "\r" in configured:
        raise ValueError("Online task scope is invalid")
    parsed = urlsplit(configured)
    if parsed.scheme and parsed.hostname:
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            raise ValueError("Online task scope must be a name or HTTPS origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Online task scope must be a name or HTTPS origin")
        return f"https://{parsed.hostname.lower()}"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", configured):
        raise ValueError("Online task scope is invalid")
    return configured
