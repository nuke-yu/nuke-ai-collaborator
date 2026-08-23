"""Fresh workspace context and reinjection assembly for tool loops."""
from __future__ import annotations


async def get_fresh_context_prefix(runner, prompt_builder) -> tuple[str, str]:
    return await prompt_builder.get_fresh_context_prefix(
        runner.bot["id"], runner.ctx.group_id,
        runner.executor.manifest.workspace.startup_files, runner.skills_xml,
    )


async def build_reinject(runner, *, compact, bot_workspace, invoked_skills_block) -> str:
    fresh_prefix, _ = await runner._get_fresh_context_prefix()
    tracker_xml = compact.build_file_tracker_xml(runner.file_tracker)
    file_contents = compact.build_file_contents_for_reinject(
        runner.file_tracker, workspace_dir=str(bot_workspace(runner.bot["id"], runner.ctx.group_id)),
    )
    invoked = invoked_skills_block(getattr(runner, "invoked_skills", {}))
    return "\n\n".join(part for part in (fresh_prefix, invoked, tracker_xml, file_contents) if part)
