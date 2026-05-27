"""Skill content transformation pipeline.

Three independent steps that can be applied in any combination:
  1. substitute_arguments  — $ARGUMENTS / $ARGUMENTS[N] / $N placeholders
  2. execute_shell_in_prompt — ```! block and !`inline` shell commands
  3. process_skill_content  — orchestrates both (convenience entry-point)

Keeping this module separate from loader.py means the pipeline can be
reused, tested, or replaced without touching skill discovery or I/O.
"""
import asyncio
import re
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"
_SHELL_BASH = ["powershell.exe", "-NoProfile", "-Command"] if _IS_WINDOWS else ["/bin/sh", "-c"]
_SHELL_PS   = ["powershell.exe", "-NoProfile", "-Command"]

# ```!\ncmd\n``` block
_BLOCK_RE  = re.compile(r'```!\s*\n?([\s\S]*?)\n?```')
# !`cmd` inline — requires leading whitespace or start-of-line
_INLINE_RE = re.compile(r'(^|[ \t])!`([^`]+)`', re.MULTILINE)


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
# Step 2 — shell command embedding
# ---------------------------------------------------------------------------

async def _run_shell_cmd(cmd: str, cwd: Path, use_powershell: bool) -> str:
    shell = _SHELL_PS if use_powershell else _SHELL_BASH
    try:
        proc = await asyncio.create_subprocess_exec(
            *shell, cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode(errors='replace').strip()
        err = stderr.decode(errors='replace').strip()
        parts = ([out] if out else []) + ([f'[stderr]\n{err}'] if err else [])
        return '\n'.join(parts) or '[no output]'
    except asyncio.TimeoutError:
        return '[shell command timed out after 30s]'
    except Exception as e:
        return f'[shell error] {e}'


async def execute_shell_in_prompt(
    content: str, skill_dir: Path, use_powershell: bool = False
) -> str:
    """Execute ```! blocks and !`inline` commands; replace each with its output.

    Commands run in parallel. Replacements applied end-to-start so earlier
    offsets stay valid.
    """
    matches: list[tuple[int, int, str, str]] = []  # (start, end, cmd, prefix)

    for m in _BLOCK_RE.finditer(content):
        matches.append((m.start(), m.end(), m.group(1).strip(), ''))

    if '!`' in content:
        for m in _INLINE_RE.finditer(content):
            matches.append((m.start(), m.end(), m.group(2).strip(), m.group(1)))

    if not matches:
        return content

    outputs = await asyncio.gather(*[
        _run_shell_cmd(cmd, skill_dir, use_powershell) for _, _, cmd, _ in matches
    ])

    for (start, end, _, prefix), output in sorted(
        zip(matches, outputs), key=lambda x: x[0][0], reverse=True
    ):
        content = content[:start] + prefix + output + content[end:]

    return content


# ---------------------------------------------------------------------------
# Convenience orchestrator
# ---------------------------------------------------------------------------

async def process_skill_content(
    content: str,
    skill_dir: Path,
    args: str = "",
    use_powershell: bool = False,
) -> str:
    """Apply the full transformation pipeline to skill content.

    Order matches claude-code: argument substitution → ${SKILL_DIR} →
    shell command execution.
    """
    content = substitute_arguments(content, args)
    content = content.replace("${SKILL_DIR}", str(skill_dir))
    content = await execute_shell_in_prompt(content, skill_dir, use_powershell)
    return content
