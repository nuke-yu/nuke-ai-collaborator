"""Prompt and message preparation for a tool-loop session."""
from __future__ import annotations

import workspace
import executors.compact as compact
from core.orchestration import prompt_builder
from core.role_router import build_image_content
from executors.plugins.tool_loop_prompt import (
    UNTRUSTED_LEARNING_POLICY,
    attach_untrusted_learning_data,
)


async def prepare_prompt_and_messages(
    runner,
    *,
    skill_discovery: bool,
    memory: str,
    learned_contexts: list,
    history: list,
    user_msg: str,
) -> tuple[object, bool]:
    """Build system prompt, user content, and resumed/current messages."""
    if skill_discovery:
        (
            runner.system_prompt_base,
            runner.skills_xml,
            runner.skills_snapshot,
            runner.always_skills,
        ) = await prompt_builder.compile_system_prompt(
            runner.bot, runner.ctx, runner.model_name, memory
        )
    else:
        from workspace.layout import get_group_language

        runner.system_prompt_base = prompt_builder.build_system_prompt_base(
            runner.bot,
            runner.ctx,
            memory,
            always_section="",
            lang=get_group_language(runner.ctx.group_id),
        )

    if runner.ctx.group_id is not None:
        group_context = await workspace.load_group_context(runner.ctx.group_id)
        if group_context:
            runner.system_prompt_base += f"\n\n{group_context}"
    if learned_contexts:
        runner.system_prompt_base += f"\n\n{UNTRUSTED_LEARNING_POLICY}"
    runner.system_prompt = runner.system_prompt_base

    user_content = build_image_content(
        user_msg, runner.ctx.file_url, runner.ctx.file_type, runner.provider
    )
    if isinstance(user_content, str):
        user_content, _ = compact.truncate_user_message(
            user_content, runner.ctx.group_id, runner.model_name
        )
    elif isinstance(user_content, list):
        for block in user_content:
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"], _ = compact.truncate_user_message(
                    block.get("text", ""), runner.ctx.group_id, runner.model_name
                )
    user_content = attach_untrusted_learning_data(user_content, learned_contexts)

    resuming = bool(runner.ctx.resume_session_id)
    if resuming:
        resumed = list(runner.ctx.resume_messages or [])
        if resumed and resumed[0].get("role") == "system":
            resumed = resumed[1:]
        runner.messages = resumed
    else:
        runner.messages = list(history) + [{"role": "user", "content": user_content}]
    return user_content, resuming
