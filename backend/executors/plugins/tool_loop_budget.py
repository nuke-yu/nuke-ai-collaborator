"""Context and memory budget policies for the tool loop."""
from __future__ import annotations

import logging
from typing import Any

from executors.plugins.tool_loop_context import drop_oldest_message_group

logger = logging.getLogger(__name__)


def apply_memory_context_budget(
    runner: Any, memory: str, learned_contexts: list[str]
) -> tuple[str, list[str]]:
    """Apply the Letta-style input budget before memory enters the prompt."""
    try:
        from ai.providers import resolve_provider_descriptor
        from memory.adapters.algorithms.letta_acl_engine import LettaOpenMemoryEngine

        descriptor = resolve_provider_descriptor(runner.provider, runner.model_name)
        if descriptor.context_window is None:
            return memory, learned_contexts
        engine = LettaOpenMemoryEngine()
        tokenizer = getattr(runner, "tokenizer", None)
        input_budget = max(1024, descriptor.context_window - int(runner.max_tokens) - 1024)
        working_set = getattr(runner, "letta_working_memory", None)
        if working_set is not None:
            paged_working = working_set.page(max(256, input_budget // 4))
            if paged_working:
                working_overlay = "\n\n".join(str(item.get("content", "")) for item in paged_working)
                memory = f"{memory}\n\n[working memory]\n{working_overlay}" if memory else working_overlay
        recall_text = "\n\n".join(value for value in learned_contexts if value)
        memory_tokens = engine.estimate_tokens(memory, tokenizer)
        recall_tokens = engine.estimate_tokens(recall_text, tokenizer)
        if memory_tokens + recall_tokens <= input_budget:
            return memory, learned_contexts
        recall_budget = min(recall_tokens, max(256, input_budget // 3))
        memory_budget = max(256, input_budget - recall_budget)
        bounded_memory = engine.truncate_text_to_tokens(memory, memory_budget, tokenizer)
        recall_records = [
            {"content": value, "importance": 1.0 - (index * 0.001)}
            for index, value in enumerate(learned_contexts) if value
        ]
        paged = engine.page_memory(recall_records, recall_budget, tokenizer)
        paged_recall = "\n\n".join(str(item["content"]) for item in paged)
        bounded_recall = engine.truncate_text_to_tokens(paged_recall, recall_budget, tokenizer)
        bounded_contexts = [bounded_recall] if bounded_recall else []
        logger.warning(
            "memory context budget applied provider=%s model=%s memory_tokens=%d recall_tokens=%d budget=%d",
            runner.provider, runner.model_name, memory_tokens, recall_tokens, input_budget,
        )
        return bounded_memory, bounded_contexts
    except (AttributeError, TypeError, ValueError):
        logger.warning("memory context budget unavailable; preserving original context", exc_info=True)
        return memory, learned_contexts


def enforce_final_context_budget(runner: Any) -> None:
    """Account for final tool schemas before making the model call."""
    try:
        from ai.providers import resolve_provider_descriptor
        from memory.adapters.algorithms.letta_acl_engine import LettaOpenMemoryEngine

        descriptor = resolve_provider_descriptor(runner.provider, runner.model_name)
        if descriptor.context_window is None:
            return
        engine = LettaOpenMemoryEngine()
        tokenizer = getattr(runner, "tokenizer", None)
        working_memory = "\n".join(
            str(message.get("content") or "")
            for message in getattr(runner, "messages", ()) if isinstance(message, dict)
        )
        allocation = engine.calculate_context_budget(
            max_tokens=descriptor.context_window,
            system_prompt=str(getattr(runner, "system_prompt", "")),
            working_memory=working_memory,
            recall_memory="",
            tool_schemas=getattr(runner, "tool_schemas", ()) or (),
            reserve_generation_tokens=max(256, int(runner.max_tokens)),
        )
        if allocation.available_for_generation <= 256:
            messages = list(getattr(runner, "messages", ()) or ())
            while len(messages) > 1 and allocation.available_for_generation <= 256:
                pruned = drop_oldest_message_group(messages)
                if len(pruned) == len(messages):
                    break
                messages = pruned
                runner.messages = messages
                working_memory = "\n".join(
                    str(message.get("content") or "")
                    for message in messages if isinstance(message, dict)
                )
                allocation = engine.calculate_context_budget(
                    max_tokens=descriptor.context_window,
                    system_prompt=str(getattr(runner, "system_prompt", "")),
                    working_memory=working_memory,
                    recall_memory="",
                    tool_schemas=getattr(runner, "tool_schemas", ()) or (),
                    reserve_generation_tokens=256,
                )
            if allocation.available_for_generation <= 0:
                runner.tool_schemas = list(getattr(runner, "tool_schemas", ()) or ())[:32]
                runner.system_prompt = engine.truncate_text_to_tokens(
                    str(getattr(runner, "system_prompt", "")),
                    max(512, int(descriptor.context_window * 0.45)), tokenizer,
                )
                allocation = engine.calculate_context_budget(
                    max_tokens=descriptor.context_window,
                    system_prompt=runner.system_prompt,
                    working_memory=working_memory,
                    recall_memory="",
                    tool_schemas=runner.tool_schemas,
                    reserve_generation_tokens=256,
                )
        if allocation.is_budget_exceeded:
            previous = int(runner.max_tokens)
            runner.max_tokens = max(256, min(previous, allocation.available_for_generation))
            logger.warning(
                "final model context budget exceeded provider=%s model=%s available_generation=%d previous_generation=%d",
                runner.provider, runner.model_name, allocation.available_for_generation, previous,
            )
    except (AttributeError, TypeError, ValueError):
        logger.warning("final model context budget unavailable", exc_info=True)
