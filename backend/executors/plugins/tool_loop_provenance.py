"""Provenance link builders for tool-loop memory and skill evidence."""
from __future__ import annotations


def tool_evidence_links(memory_refs: list[str], dispatch_context: dict) -> list[dict]:
    """Build causal links after tool arguments pass provenance validation."""
    from sessions.evidence import evidence_kind

    links = [
        {
            "kind": evidence_kind(ref),
            "ref": ref,
            "relation": "cited",
            "metadata": {"source": "validated_tool_argument"},
        }
        for ref in memory_refs
    ]
    skill_link = dispatch_context.get("skill_evidence_link")
    if isinstance(skill_link, dict):
        links.append(skill_link)
    return links


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
