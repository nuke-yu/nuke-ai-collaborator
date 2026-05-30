"""Skill content transformation pipeline.

Two steps that can be applied in any combination:
  1. substitute_arguments  — $ARGUMENTS / $ARGUMENTS[N] / $N placeholders
  2. process_skill_content  — orchestrates substitution + ${SKILL_DIR}

NOTE (DFT-022): skill bodies do NOT execute embedded shell. The old
```! / !`inline` mechanism ran commands via /bin/sh during skill loading,
bypassing tool_executor's denylist + permission pipeline + sandbox — a bot
that can write_file + run_skill could self-write a skill with an embedded `!`
block and get arbitrary host code execution. Any shell work a skill needs
must go through the run_shell tool (hook/permission/sandbox guarded); `!`
markers in skill text are now inert and passed through verbatim.

Keeping this module separate from loader.py means the pipeline can be
reused, tested, or replaced without touching skill discovery or I/O.
"""
import re


# ---------------------------------------------------------------------------
# Step 1 — argument substitution
# ---------------------------------------------------------------------------

def substitute_arguments(content: str, args: str) -> str:
    """Replace $ARGUMENTS, $ARGUMENTS[N], and $N placeholders with args.

    If no placeholder is found but args is non-empty, appends
    'ARGUMENTS: {args}' to the content (same behaviour as claude-code).
    """
    if not args:
        return content

    parts = args.split()
    original = content

    content = re.sub(
        r'\$ARGUMENTS\[(\d+)\]',
        lambda m: parts[int(m.group(1))] if int(m.group(1)) < len(parts) else '',
        content,
    )
    content = re.sub(
        r'\$(\d+)(?!\w)',
        lambda m: parts[int(m.group(1))] if int(m.group(1)) < len(parts) else '',
        content,
    )
    content = content.replace('$ARGUMENTS', args)

    if content == original:
        content += f'\n\nARGUMENTS: {args}'
    return content


# ---------------------------------------------------------------------------
# Convenience orchestrator
# ---------------------------------------------------------------------------

async def process_skill_content(
    content: str,
    skill_dir,
    args: str = "",
) -> str:
    """Apply the transformation pipeline to skill content.

    Order matches claude-code: argument substitution → ${SKILL_DIR}.
    Embedded `!` shell blocks are intentionally NOT executed (DFT-022).
    """
    content = substitute_arguments(content, args)
    content = content.replace("${SKILL_DIR}", str(skill_dir))
    return content
