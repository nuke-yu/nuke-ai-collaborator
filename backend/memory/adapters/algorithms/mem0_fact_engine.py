"""Mem0 Fact Extraction & Reconciliation Engine (Apache-2.0 ported algorithm).

Ported from mem0 (mem0ai) memory management pipeline:
- Extract factual statements from natural conversation traces.
- Compare candidate facts against active memory records.
- Reconcile changes into ADD, UPDATE, DELETE, or NOOP actions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence


class FactActionType(StrEnum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    NOOP = "NOOP"


@dataclass(frozen=True, slots=True)
class FactAction:
    action_type: FactActionType
    content: str
    target_record_id: str | None = None
    old_content: str | None = None
    confidence: float = 1.0
    reason: str = ""


MEM0_SYSTEM_PROMPT = """You are a Memory Management Agent adhering to the Mem0 specification.
Your goal is to analyze user conversation input alongside existing memory records and extract atomic facts, then reconcile each fact into an action: ADD, UPDATE, DELETE, or NOOP.

Rules:
- ADD: A new fact about the user or project that is not present in existing records.
- UPDATE: A fact that updates or modifies an existing record's attribute/value (specify target_record_id and old_content).
- DELETE: User explicitly refutes or invalidates an existing record (specify target_record_id).
- NOOP: Fact already exists in memory records or is non-factual chatter.

Return ONLY a JSON array of objects:
[
  {
    "action": "ADD" | "UPDATE" | "DELETE" | "NOOP",
    "fact": "statement",
    "target_record_id": "record_id or null",
    "old_content": "old_text or null",
    "reason": "short explanation"
  }
]
"""


class Mem0FactEngine:
    """Audit-grade fact extraction and conflict reconciliation engine (Supports LLM Prompt & Rule Fallback)."""

    # Common chatter prefixes and non-factual phrases
    _CHATTER_RE = re.compile(
        r"^(hi|hello|hey|thanks|thank you|ok|okay|got it|sure|cool|good morning|good evening)[\s!.,]*$",
        re.IGNORECASE,
    )
    _NEGATION_PREFIXES = ("no longer ", "don't ", "do not ", "stop using ", "cancel ", "remove ")

    def extract_candidate_facts(self, text: str) -> list[str]:
        """Split text into candidate atomic factual statements."""
        if not text or not text.strip():
            return []

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        candidates: list[str] = []

        for line in lines:
            # Split by punctuation (period, exclamation, semicolon, chinese period)
            sentences = re.split(r"[.!\n；。！]", line)
            for s in sentences:
                cleaned = s.strip()
                if not cleaned:
                    continue
                if self._CHATTER_RE.match(cleaned):
                    continue
                if len(cleaned) < 3:
                    continue
                candidates.append(cleaned)

        return candidates

    async def extract_and_reconcile(
        self,
        text: str,
        existing_records: Sequence[Mapping[str, Any]] = (),
        *,
        ai_call_fn: Any = None,
    ) -> list[FactAction]:
        """Run Mem0's complete extraction→decision pipeline.

        The LLM path is preferred when supplied; malformed/failed responses
        fall back to the deterministic extractor so observation never loses a
        fact solely because a provider returned invalid JSON.
        """
        if ai_call_fn is not None:
            actions = await self.reconcile_with_llm(text, existing_records, ai_call_fn=ai_call_fn)
            if actions:
                return actions
        return [self.reconcile_fact(existing_records, fact) for fact in self.extract_candidate_facts(text)]

    def reconcile_fact(
        self, existing_records: Sequence[Mapping[str, Any]], new_fact: str
    ) -> FactAction:
        """Compare new candidate fact against existing facts and determine action."""
        new_norm = self._normalize(new_fact)
        if not new_norm:
            return FactAction(
                action_type=FactActionType.NOOP,
                content=new_fact,
                reason="empty_or_invalid_fact",
            )

        # Check for explicit refutation / deletion requests
        refutation_target = self._detect_refutation(new_fact, existing_records)
        if refutation_target:
            return FactAction(
                action_type=FactActionType.DELETE,
                content=new_fact,
                target_record_id=str(refutation_target.get("record_id")),
                old_content=str(refutation_target.get("content", "")),
                confidence=0.95,
                reason="explicit_refutation_detected",
            )

        # Search for identical or conflicting records
        best_match = None
        best_similarity = 0.0

        for rec in existing_records:
            existing_content = str(rec.get("content") or "")
            existing_norm = self._normalize(existing_content)
            if not existing_norm:
                continue

            similarity = self._compute_similarity(new_norm, existing_norm)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = rec

        if best_match and best_similarity >= 0.90:
            return FactAction(
                action_type=FactActionType.NOOP,
                content=new_fact,
                target_record_id=str(best_match.get("record_id")),
                old_content=str(best_match.get("content", "")),
                confidence=1.0,
                reason="duplicate_fact_found",
            )

        # Check for attribute update (same topic/subject, changed value)
        conflict_target = self._detect_attribute_update(new_fact, existing_records)
        if conflict_target:
            return FactAction(
                action_type=FactActionType.UPDATE,
                content=new_fact,
                target_record_id=str(conflict_target.get("record_id")),
                old_content=str(conflict_target.get("content", "")),
                confidence=0.90,
                reason="attribute_value_update",
            )

        return FactAction(
            action_type=FactActionType.ADD,
            content=new_fact,
            confidence=0.85,
            reason="new_fact_discovered",
        )

    _STOPWORDS = {
        "user", "bot", "the", "a", "an", "in", "on", "at", "for", "with", "is", "are",
        "was", "were", "be", "been", "uses", "use", "using", "has", "have", "had",
    }

    def _detect_refutation(
        self, new_fact: str, existing_records: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any] | None:
        lowered = new_fact.lower()
        if not any(prefix in lowered for prefix in self._NEGATION_PREFIXES):
            return None

        # Extract target subject after negation keyword
        target_clause = lowered
        for prefix in self._NEGATION_PREFIXES:
            if prefix in lowered:
                target_clause = lowered.split(prefix, 1)[-1]
                break

        target_norm = self._normalize(target_clause)
        target_tokens = set(target_norm.split()) - self._STOPWORDS
        if not target_tokens:
            return None

        for rec in existing_records:
            existing_norm = self._normalize(str(rec.get("content") or ""))
            existing_tokens = set(existing_norm.split()) - self._STOPWORDS
            if not existing_tokens:
                continue
            containment = len(target_tokens & existing_tokens) / len(target_tokens)
            if containment >= 0.5:
                return rec
        return None


    def _detect_attribute_update(
        self, new_fact: str, existing_records: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any] | None:
        new_norm = self._normalize(new_fact)
        new_tokens = set(new_norm.split()) - self._STOPWORDS

        for rec in existing_records:
            existing_norm = self._normalize(str(rec.get("content") or ""))
            existing_tokens = set(existing_norm.split()) - self._STOPWORDS

            # Overlap in significant subject tokens
            common = new_tokens & existing_tokens
            if len(common) >= 2 and new_norm != existing_norm:
                jaccard = len(common) / max(1, len(new_tokens | existing_tokens))
                if 0.5 <= jaccard < 0.9:
                    return rec
        return None


    @staticmethod
    def _normalize(text: str) -> str:
        cleaned = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", (text or "").lower())
        return " ".join(cleaned.split())

    @staticmethod
    def _compute_similarity(text1: str, text2: str) -> float:
        if text1 == text2:
            return 1.0
        t1 = set(text1.split())
        t2 = set(text2.split())
        if not t1 or not t2:
            return 0.0
        intersection = t1 & t2
        union = t1 | t2
        return len(intersection) / len(union)

    async def reconcile_with_llm(
        self,
        user_message: str,
        existing_records: Sequence[Mapping[str, Any]],
        ai_call_fn: Any = None,
        model: str = "deepseek-chat",
        provider: str = "deepseek",
    ) -> list[FactAction]:
        """Perform LLM Prompt-based fact extraction and reconciliation (Mem0 Specification)."""
        if not user_message or not user_message.strip():
            return []

        formatted_records = [
            {"record_id": str(r.get("record_id")), "content": str(r.get("content"))}
            for r in existing_records
        ]
        prompt = (
            f"User Input:\n{user_message}\n\n"
            f"Existing Memory Records:\n{formatted_records}\n\n"
            "Extract facts and reconcile into JSON list according to system prompt rules."
        )

        try:
            if ai_call_fn is None:
                from ai.client import call_ai_once
                ai_call_fn = call_ai_once

            res = await ai_call_fn(
                MEM0_SYSTEM_PROMPT,
                [{"role": "user", "content": prompt}],
                provider=provider,
                model=model,
                temperature=0.1,
            )
            content = res.get("content", "") if isinstance(res, dict) else str(res)
            # Parse JSON from response
            import json
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if not match:
                raise ValueError("No JSON array found in LLM output")
            items = json.loads(match.group(0))

            actions: list[FactAction] = []
            for item in items:
                act_str = str(item.get("action", "ADD")).upper()
                try:
                    act_type = FactActionType(act_str)
                except ValueError:
                    act_type = FactActionType.ADD

                actions.append(
                    FactAction(
                        action_type=act_type,
                        content=str(item.get("fact", "")),
                        target_record_id=item.get("target_record_id"),
                        old_content=item.get("old_content"),
                        confidence=0.95,
                        reason=str(item.get("reason", "llm_reconciled")),
                    )
                )
            if actions:
                return actions
        except Exception:
            # Fall back smoothly to rule-based engine if LLM call fails or returns non-JSON
            pass

        # Fallback to rule-based extraction
        candidates = self.extract_candidate_facts(user_message)
        return [self.reconcile_fact(existing_records, c) for c in candidates]
