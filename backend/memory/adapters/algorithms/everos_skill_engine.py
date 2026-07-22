"""EverOS Agent Skill Extractor Engine (Apache-2.0 ported algorithm).

Ported from EverOS (everalgo) Skill Learning pipeline:
- Compile candidate skills from case clusters.
- Synthesize trigger keywords and tool invocation sequences.
- Render SKILL.md template with execution steps and verification rules.
- Enforce qualification gating (Min Case Count >= 3, Success Rate >= 0.8).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from memory.adapters.algorithms.everos_case_engine import ExtractedCase
from memory.adapters.algorithms.everos_clustering_engine import CaseCluster


@dataclass(frozen=True, slots=True)
class SkillCandidate:
    skill_id: str
    title: str
    description: str
    trigger_keywords: tuple[str, ...]
    tools_sequence: tuple[str, ...]
    skill_md_content: str
    qualification_score: float
    is_qualified: bool


class EverOSSkillEngine:
    """Audit-grade Skill Extractor and SKILL.md compilation engine."""

    def __init__(
        self,
        min_cases: int = 3,
        min_success_rate: float = 0.8,
        min_qualification_score: float = 0.7,
    ) -> None:
        self.min_cases = min_cases
        self.min_success_rate = min_success_rate
        self.min_qualification_score = min_qualification_score

    def compile_skill_candidate(self, cluster: CaseCluster) -> SkillCandidate | None:
        """Compile case cluster into a structured SkillCandidate with SKILL.md markdown."""
        if not cluster.cases:
            return None

        total_cases = len(cluster.cases)
        successful_cases = sum(1 for c in cluster.cases if c.outcome == "completed")
        success_rate = successful_cases / float(total_cases) if total_cases > 0 else 0.0

        # Extract tool sequence frequency
        tool_counts: dict[str, int] = {}
        all_keywords: set[str] = set()

        for c in cluster.cases:
            for tool in c.tools_used:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            words = re.findall(r"\b\w{3,}\b", c.task.lower())
            all_keywords.update(words[:10])

        top_tools = tuple(
            t for t, _ in sorted(tool_counts.items(), key=lambda item: item[1], reverse=True)
        )
        triggers = tuple(sorted(all_keywords)[:8])

        primary_task = cluster.cases[0].task
        title = f"Skill: {primary_task[:50]}"
        skill_id = f"skill:{cluster.centroid_signature[:12]}"

        qualification_score = min(1.0, (total_cases / float(self.min_cases)) * 0.5 + success_rate * 0.5)
        is_qualified = (
            total_cases >= self.min_cases
            and success_rate >= self.min_success_rate
            and qualification_score >= self.min_qualification_score
        )

        skill_md = self._render_skill_md(
            skill_id=skill_id,
            title=title,
            description=f"Auto-synthesized skill for: {primary_task}",
            triggers=triggers,
            tools=top_tools,
            sample_cases=cluster.cases,
        )

        return SkillCandidate(
            skill_id=skill_id,
            title=title,
            description=f"Auto-synthesized skill for: {primary_task}",
            trigger_keywords=triggers,
            tools_sequence=top_tools,
            skill_md_content=skill_md,
            qualification_score=qualification_score,
            is_qualified=is_qualified,
        )

    @staticmethod
    def _sanitize_yaml_field(text: str) -> str:
        """Sanitize text for safe YAML frontmatter rendering."""
        if not text:
            return ""
        clean = text.replace("---", "").replace('"', '\\"').replace("\n", " ").replace("\r", "")
        return clean.strip()

    def _render_skill_md(
        self,
        skill_id: str,
        title: str,
        description: str,
        triggers: Sequence[str],
        tools: Sequence[str],
        sample_cases: Sequence[ExtractedCase],
    ) -> str:
        """Render SKILL.md template content."""
        clean_title = self._sanitize_yaml_field(title)
        clean_desc = self._sanitize_yaml_field(description)
        clean_triggers = [self._sanitize_yaml_field(t) for t in triggers]
        clean_tools = [self._sanitize_yaml_field(t) for t in tools]

        trigger_str = ", ".join(clean_triggers) if clean_triggers else "general"
        tool_str = " -> ".join(clean_tools) if clean_tools else "N/A"
        cases_str = "\n".join(
            f"- Case `{c.case_id}`: {self._sanitize_yaml_field(c.task)[:80]} ({c.outcome})"
            for c in sample_cases[:3]
        )

        return f"""---
name: {skill_id}
title: "{clean_title}"
description: "{clean_desc}"
triggers: [{trigger_str}]
tools: [{tool_str}]
---

# {clean_title}

## Description
{clean_desc}

## Required Tools
{tool_str}

## Execution Steps
1. Parse input parameters and verify environment readiness.
2. Execute tool workflow sequence in order: `{tool_str}`.
3. Validate output signals and handle potential step errors.

## Proven Use Cases
{cases_str}
"""
