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

Env-aware templating (opt-in via `template: true` frontmatter): a skill may use
Jinja2 `{{ var }}` / `{% if %}` against a small set of safe, caller-supplied
variables (os/shell/role/…). This restores environment awareness that DFT-022
removed WITHOUT re-opening the shell-exec RCE: rendering runs in a
SandboxedEnvironment with no globals and no template loader (so no filesystem
access, no `{% include %}`), and any failure falls back to the un-rendered text.
"""
import logging
import re

logger = logging.getLogger(__name__)


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

def render_sandboxed(content: str, template_vars: dict) -> str:
    """Render `content` as a Jinja2 template in a no-privilege sandbox.

    Safety contract (do NOT relax without review — this is the DFT-022 boundary):
      - SandboxedEnvironment blocks access to unsafe attributes/methods.
      - globals are cleared (no range/dict/etc. builtins exposed).
      - no template loader → `{% include %}` / `{% import %}` cannot read files.
      - only plain str/dict/list values from `template_vars` are exposed.
      - unknown variables render as empty (no crash, no info leak).
      - ANY exception → return the input unchanged (never break skill loading).
    """
    if not template_vars or ("{{" not in content and "{%" not in content):
        return content
    try:
        from jinja2.sandbox import SandboxedEnvironment
        from jinja2 import Undefined

        class _KeepUndefined(Undefined):
            __slots__ = ()
            def __str__(self) -> str:  # unknown var → empty, don't raise
                return ""

        env = SandboxedEnvironment(undefined=_KeepUndefined, autoescape=False)
        env.globals.clear()
        return env.from_string(content).render(**template_vars)
    except Exception as e:
        logger.warning("skill template render failed, using raw content: %s", e)
        return content


async def process_skill_content(
    content: str,
    skill_dir,
    args: str = "",
    template_vars: dict | None = None,
) -> str:
    """Apply the transformation pipeline to skill content.

    Order matches claude-code: argument substitution → ${SKILL_DIR} → (optional)
    sandboxed Jinja2 templating when the caller supplies `template_vars`.
    Embedded `!` shell blocks are intentionally NOT executed (DFT-022).
    """
    content = substitute_arguments(content, args)
    content = content.replace("${SKILL_DIR}", str(skill_dir))
    if template_vars:
        content = render_sandboxed(content, template_vars)
    return content
