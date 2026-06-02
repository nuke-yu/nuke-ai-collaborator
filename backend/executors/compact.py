"""
executors/compact.py — AutoCompact pipeline (5 strategies), referencing Claude Code design.

Execution order per query turn (called from tool_loop_v1):
  1. apply_tool_result_microcompact()  — replace stale tool results (count-based, no AI)
  2. snip_if_needed()                  — drop oldest user/assistant pair (no AI)
  3. auto_compact_if_needed()          — session-memory first, AI-full fallback
     ├─ _try_session_memory_compact()  — reuse existing 【历史摘要】 as base
     └─ _ai_compact()                  — 9-section structured AI summary
  (4) Cached Microcompact              — Claude provider only, via call_ai_once flag
  (5) Post-run: maybe_compact_db_history() — soft-delete old DB messages, save summary
"""
from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Optional

from core import config
from ai.client import call_ai_once

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — aligned with Claude Code's autoCompact.ts
# ---------------------------------------------------------------------------
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000   # reserve for summary output (p99.99 = 17,387)
AUTOCOMPACT_BUFFER_TOKENS     = config.AUTOCOMPACT_BUFFER_TOKENS   # headroom buffer before threshold
MAX_CONSECUTIVE_FAILURES      = 3        # circuit-breaker trip count

MC_KEEP_RECENT_TOOL_RESULTS   = 5        # keep last N tool results intact (Strategy 1)
MC_CLEARED_MARKER             = "[旧工具结果已清除]"

SNIP_KEEP_PAIRS               = 4        # min user/assistant pairs to keep (Strategy 2)

_PRE_RUN_TOKEN_THRESHOLD      = config.PRE_RUN_TOKEN_THRESHOLD   # pre-run compaction trigger
_DB_COMPACTION_TOKEN_THRESHOLD = config.DB_COMPACTION_TOKEN_THRESHOLD  # post-run DB compaction trigger
_DB_COMPACTION_KEEP_RECENT    = 10       # DB messages protected from soft-delete

REINJECT_CONTEXT_BUDGET       = 25_000   # max chars re-injected after compaction
REINJECT_MAX_FILES            = 5        # caller should pass at most this many files

# Tools whose results are candidates for microcompact clearing
_MICROCOMPACT_TOOLS = {"run_shell", "read_file", "read_local_file", "list_workspace", "run_skill"}

# File-operation tool sets for cross-compaction tracking
_FILE_READ_TOOLS  = {"read_file", "read_local_file"}
_FILE_WRITE_TOOLS = {"write_file", "write_local_file"}

# Context window sizes per model (tokens)
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-chat":      64_000,
    "deepseek-reasoner":  64_000,
    "gpt-4o":            128_000,
    "gpt-4o-mini":       128_000,
    "gpt-4-turbo":       128_000,
    "claude-opus-4-7":   200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5":  200_000,
    "qwen-max":           32_000,
    "qwen-plus":         131_072,
}
_DEFAULT_CONTEXT_WINDOW = 64_000

# Circuit breaker: group_id → consecutive failure count
_compaction_failures: dict[int, int] = {}

# DB-level compaction concurrency guard
_db_compaction_locks: set[int] = set()


# ---------------------------------------------------------------------------
# Token estimation & threshold helpers
# ---------------------------------------------------------------------------

_PER_MESSAGE_OVERHEAD = 8  # rough allowance for role/keys/braces structure


def _content_chars(content) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    # list/multimodal or other non-string content — rare and small
    return len(str(content))


def estimate_tokens(messages: list[dict]) -> int:
    """Coarse token estimate ≈ serialised char length / 4.

    Sums per-message content length plus a small structural overhead instead of
    json.dumps-ing the whole array on every call. estimate_tokens runs several
    times per turn (snip, auto-compact, session-memory, db-compaction); the old
    full-array json.dumps allocated and escaped one giant string each time.
    """
    total = 0
    for m in messages:
        total += _content_chars(m.get("content"))
        total += len(m.get("name") or "") + _PER_MESSAGE_OVERHEAD
    return total // 4


def inject_context_after_compact(
    messages: list[dict],
    context_text: str,
    budget: int = REINJECT_CONTEXT_BUDGET,
) -> list[dict]:
    """Re-inject workspace context into the compacted summary message.

    After compaction the original context prefix is gone; this merges it back
    (up to `budget` chars) so the bot retains workspace file awareness.
    Returns a new list — never mutates input.
    Handles both string and list (multi-modal) message content.
    """
    if not messages or not context_text or not context_text.strip():
        return messages

    snippet = context_text[:budget]
    if len(context_text) > budget:
        snippet += f"\n\n[...工作区文件已截断至 {budget:,} 字符]"
    header = f"【工作区文件（压缩后刷新）】\n{snippet}"

    head = messages[0]
    existing = head.get("content", "")
    if isinstance(existing, list):
        new_content: str | list = [{"type": "text", "text": header}] + existing
    else:
        new_content = f"{header}\n\n{existing}" if existing else header

    return [{**head, "content": new_content}] + messages[1:]


def build_file_tracker_xml(tracker: dict[str, str]) -> str:
    """Serialise the file-operation tracker to XML for re-injection after compaction.

    tracker: {path: "read" | "modified"}
    Modified status takes priority: a file read then written is recorded as modified only.
    Returns empty string when tracker is empty.
    """
    if not tracker:
        return ""
    read_paths     = sorted(p for p, s in tracker.items() if s == "read")
    modified_paths = sorted(p for p, s in tracker.items() if s == "modified")
    lines = ["<file_operations>"]
    for p in read_paths:
        lines.append(f"  <read>{p}</read>")
    for p in modified_paths:
        lines.append(f"  <modified>{p}</modified>")
    lines.append("</file_operations>")
    return "\n".join(lines)


def build_file_contents_for_reinject(
    tracker: dict[str, str],
    workspace_dir: str | None = None,
    max_files: int = REINJECT_MAX_FILES,
    budget: int = REINJECT_CONTEXT_BUDGET,
) -> str:
    """Read tracked files and return their contents as XML for re-injection after compaction.

    Modified files are prioritised over read-only files.
    Relative paths are resolved against workspace_dir; absolute paths are read directly.
    Returns empty string when tracker is empty or no files are readable.
    """
    if not tracker:
        return ""

    modified = [p for p, s in tracker.items() if s == "modified"]
    read_only = [p for p, s in tracker.items() if s == "read"]
    candidates = (modified + read_only)[:max_files]

    parts: list[str] = []
    used = 0

    for raw_path in candidates:
        try:
            p = Path(raw_path)
            if not p.is_absolute() and workspace_dir:
                p = Path(workspace_dir) / p
            content = p.read_text(encoding="utf-8")
        except Exception:
            continue

        if not content:
            continue

        remaining = budget - used
        if remaining <= 0:
            break

        header = f'  <file path="{raw_path}">'
        footer = "  </file>"
        overhead = len(header) + len(footer) + 2

        if len(content) + overhead > remaining:
            available = remaining - overhead - 30
            if available < 100:
                break
            content = content[:available] + "\n[...已截断]"

        entry = f"{header}\n{content}\n{footer}"
        parts.append(entry)
        used += len(entry) + 1

    if not parts:
        return ""

    return "<file_contents>\n" + "\n".join(parts) + "\n</file_contents>"


def autocompact_threshold(model_name: str) -> int:
    """Token count above which AI compaction triggers.

    Formula (from Claude Code):
        effectiveWindow = contextWindow - MAX_OUTPUT_TOKENS_FOR_SUMMARY
        threshold       = effectiveWindow - AUTOCOMPACT_BUFFER_TOKENS
    """
    window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
    return window - MAX_OUTPUT_TOKENS_FOR_SUMMARY - AUTOCOMPACT_BUFFER_TOKENS


def snip_threshold(model_name: str) -> int:
    """Snip fires at 70% of context window — before AI compaction headroom runs out."""
    window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
    return int(window * 0.70)


# ---------------------------------------------------------------------------
# Strategy 1: Count-Based Microcompact
# (Claude Code: Time-Based Microcompact, adapted for count instead of cache TTL)
# ---------------------------------------------------------------------------

def apply_tool_result_microcompact(messages: list[dict]) -> list[dict]:
    """Replace older compactable tool-result content with a cleared marker.

    Keeps the most recent MC_KEEP_RECENT_TOOL_RESULTS intact.
    Preserves message structure (role, tool_call_id, name) — only content changes.
    Returns a new list; never mutates input.
    """
    compactable_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and m.get("name", "") in _MICROCOMPACT_TOOLS
    ]
    to_clear = (
        compactable_indices[:-MC_KEEP_RECENT_TOOL_RESULTS]
        if len(compactable_indices) > MC_KEEP_RECENT_TOOL_RESULTS
        else []
    )
    if not to_clear:
        return messages

    result = list(messages)
    for idx in to_clear:
        if result[idx].get("content") != MC_CLEARED_MARKER:
            m = dict(result[idx])
            m["content"] = MC_CLEARED_MARKER
            result[idx] = m
    return result


# ---------------------------------------------------------------------------
# Strategy 2: Snip
# (Claude Code: Snip strategy — prune oldest user/assistant pair)
# ---------------------------------------------------------------------------

def _count_conversation_pairs(messages: list[dict]) -> int:
    """Count complete user→assistant exchange pairs (tool messages ignored)."""
    pairs = 0
    last_role = None
    for m in messages:
        role = m.get("role")
        if role in ("user", "assistant"):
            if role == "assistant" and last_role == "user":
                pairs += 1
            last_role = role
    return pairs


def snip_if_needed(
    messages: list[dict], model_name: str
) -> tuple[list[dict], int]:
    """Remove the oldest plain user/assistant pair when over snip threshold.

    Rules:
    - Never removes a pair that has tool messages between user and assistant
      (would orphan tool_result messages).
    - Keeps at least SNIP_KEEP_PAIRS pairs.
    - Returns (new_messages, tokens_freed).
    """
    if estimate_tokens(messages) <= snip_threshold(model_name):
        return messages, 0

    if _count_conversation_pairs(messages) <= SNIP_KEEP_PAIRS:
        return messages, 0

    # Find first user message
    first_user = next(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), None
    )
    if first_user is None:
        return messages, 0

    # Find the corresponding assistant response
    first_asst = next(
        (i for i in range(first_user + 1, len(messages))
         if messages[i].get("role") == "assistant"),
        None,
    )
    if first_asst is None:
        return messages, 0

    # Abort if tool messages sit between user and assistant (structural dependency)
    between = messages[first_user + 1:first_asst]
    if any(m.get("role") == "tool" for m in between):
        return messages, 0

    freed_tokens = estimate_tokens(messages[first_user:first_asst + 1])
    new_messages = messages[:first_user] + messages[first_asst + 1:]
    return new_messages, freed_tokens


# ---------------------------------------------------------------------------
# Strategy 3: Session Memory Compaction
# (Claude Code: trySessionMemoryCompaction — reuse existing summary as base)
# ---------------------------------------------------------------------------

async def _try_session_memory_compact(
    messages: list[dict],
    system_prompt: str,
    provider: str,
    model_name: str,
    temperature: float,
) -> Optional[list[dict]]:
    """Reuse an existing 【历史摘要】 message as base; summarise only the delta.

    If messages[0] is already a summary (from a prior DB compaction), and the
    delta (remaining messages) still fits within the threshold, no action is
    needed.  If the delta is itself too large, summarise it with a PARTIAL prompt
    and combine with the base summary.

    Returns compacted messages, or None (fall through to full AI compaction).
    """
    if not messages:
        return None

    first = messages[0]
    content = first.get("content") or ""
    if not (first.get("role") == "user" and content.startswith("【历史摘要】")):
        return None  # No existing session-memory base — fall through

    base_summary = content
    recent = messages[1:]

    if estimate_tokens(recent) <= autocompact_threshold(model_name):
        return None  # Delta small enough — no recompaction needed

    # Summarise only the delta with a partial/incremental prompt
    lines = []
    for m in recent:
        role = m.get("role", "")
        mc = m.get("content") or ""
        if isinstance(mc, list):
            mc = str(mc)
        if role == "tool":
            lines.append(f"[工具结果 {m.get('name', '')}]: {mc[:500]}")
        else:
            lines.append(f"[{role}]: {mc[:1000]}")
    history_text = "\n".join(lines)

    partial_prompt = (
        "你是一个对话摘要助手。以下是在已有摘要之后发生的新对话。"
        "请简洁地总结其中的关键决策、操作和结论，用中文，100-300字。"
    )
    try:
        result = await call_ai_once(
            partial_prompt,
            [{"role": "user", "content": f"新对话：\n\n{history_text}"}],
            provider, model_name, temperature, 1024,
        )
        delta_summary = result["content"] if result["type"] == "text" else ""
    except Exception as exc:
        logger.warning("Session-memory delta summarisation failed: %s", exc)
        return None

    if not delta_summary:
        return None

    combined = f"{base_summary}\n\n【追加摘要】\n{delta_summary}"
    return [{"role": "user", "content": combined}]


# ---------------------------------------------------------------------------
# Strategy 4: Full AI Compaction with 9-Section Structured Prompt
# (Claude Code: compactConversation + prompt.ts BASE_COMPACT_PROMPT)
# ---------------------------------------------------------------------------

_NO_TOOLS_PREAMBLE = (
    "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n"
    "Your entire response must be an <analysis> block followed by a <summary> block.\n\n"
)

_COMPACT_SYSTEM_PROMPT = _NO_TOOLS_PREAMBLE + """\
Your task is to create a detailed summary of the conversation so far, paying close attention \
to the user's explicit requests and previous actions. This summary will replace the full \
conversation history to preserve context while fitting within the context window.

CRITICAL INSTRUCTION: If you encounter contents from files named 'SPEC.md' or 'BOARD.md' \
in the conversation, you MUST preserve their core structure and key requirements in your summary. \
Do not over-compress business requirements or Jira ticket formats.

Before writing the summary, use an <analysis> block to organize your thoughts \
(this block will be stripped from the final output).

Then write a <summary> block with exactly these 9 sections:

1. Primary Request and Intent
   All the user's explicit requests and goals in detail.

2. Key Technical Concepts
   Important frameworks, APIs, patterns, and constraints discussed.

3. Files and Code Sections
   All files read, written, or referenced — with full code snippets where relevant \
and a note on why each file matters.

4. Errors and Fixes
   Every error encountered and how it was resolved. Include user feedback on fixes.

5. Problem Solving
   Approaches tried, trade-offs considered, key decisions made.

6. All User Messages
   A complete ordered list of every user instruction or question (not tool results).

7. Pending Tasks
   Tasks explicitly requested but not yet completed.

8. Current Work
   What was being worked on immediately before this summary, with file names and snippets.

9. Optional Next Step
   The single most logical next action based on the most recent user request. \
Include a direct quote from the conversation showing where work was left off. \
If the last task was complete, omit this section.

Format:
<analysis>
[your reasoning]
</analysis>

<summary>
[9 sections as above]
</summary>
"""


def format_compact_summary(raw: str) -> str:
    """Strip <analysis> scratchpad and extract <summary> content."""
    cleaned = re.sub(r"<analysis>.*?</analysis>", "", raw, flags=re.DOTALL).strip()
    m = re.search(r"<summary>(.*?)</summary>", cleaned, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    return cleaned  # fallback: return full text if tags absent


async def _ai_compact(
    messages: list[dict],
    system_prompt: str,
    provider: str,
    model_name: str,
    temperature: float,
) -> Optional[list[dict]]:
    """Run full AI compaction using the 9-section structured prompt.

    Returns a single-element list with the summary message, or None on failure.
    """
    lines = []
    for m in messages:
        role = m.get("role", "")
        mc = m.get("content") or ""
        if isinstance(mc, list):
            mc = str(mc)
        if role == "tool":
            lines.append(f"[工具结果 {m.get('name', '')}]: {mc[:1000]}")
        else:
            lines.append(f"[{role}]: {mc[:2000]}")
    history_text = "\n".join(lines)

    user_msg = (
        f"系统提示（前500字）：{system_prompt[:500]}\n\n"
        f"请总结以下对话历史：\n\n{history_text}"
    )
    try:
        result = await call_ai_once(
            _COMPACT_SYSTEM_PROMPT,
            [{"role": "user", "content": user_msg}],
            provider, model_name, temperature,
            MAX_OUTPUT_TOKENS_FOR_SUMMARY,
        )
        raw = result["content"] if result["type"] == "text" else ""
    except Exception as exc:
        logger.warning("AI compaction call failed: %s", exc)
        return None

    if not raw:
        return None

    summary = format_compact_summary(raw)
    return [{"role": "user", "content": f"【历史摘要】\n{summary}"}]


# ---------------------------------------------------------------------------
# Strategy 5: Cached Microcompact gate (Claude provider only)
# ---------------------------------------------------------------------------

def should_use_cached_microcompact(provider: str) -> bool:
    """Strategy 5 requires Anthropic's context-management API — Claude only."""
    return provider == "claude"


# ---------------------------------------------------------------------------
# Circuit breaker helpers
# ---------------------------------------------------------------------------

def _circuit_open(group_id: int) -> bool:
    return _compaction_failures.get(group_id, 0) >= MAX_CONSECUTIVE_FAILURES


def _record_failure(group_id: int) -> None:
    _compaction_failures[group_id] = _compaction_failures.get(group_id, 0) + 1
    if _compaction_failures[group_id] >= MAX_CONSECUTIVE_FAILURES:
        logger.warning(
            "Compaction circuit breaker tripped for group %s after %d consecutive failures",
            group_id, _compaction_failures[group_id],
        )


def _record_success(group_id: int) -> None:
    _compaction_failures.pop(group_id, None)


# ---------------------------------------------------------------------------
# Main orchestrator: auto_compact_if_needed
# ---------------------------------------------------------------------------

async def auto_compact_if_needed(
    messages: list[dict],
    model_name: str,
    group_id: int,
    system_prompt: str,
    provider: str,
    temperature: float,
    broadcaster,
    temp_id: str,
    bot_id: int,
    context_text: str = "",
) -> tuple[list[dict], bool]:
    """Run AI compaction pipeline (Strategies 3 → 4) if context exceeds threshold.

    Callers should run apply_tool_result_microcompact (Strategy 1) and
    snip_if_needed (Strategy 2) before calling this.
    context_text: workspace context to re-inject into the summary message.

    Returns (new_messages, was_compacted).
    """
    if estimate_tokens(messages) <= autocompact_threshold(model_name):
        return messages, False

    if _circuit_open(group_id):
        return messages, False

    # Strategy 3: session-memory delta compaction
    try:
        result = await _try_session_memory_compact(
            messages, system_prompt, provider, model_name, temperature
        )
    except Exception as exc:
        logger.warning("Strategy 3 error: %s", exc)
        result = None

    if result is not None:
        _record_success(group_id)
        await interaction.broadcast(group_id, {
            "type": "compaction", "temp_id": temp_id,
            "strategy": "session_memory",
            "message": "上下文已通过增量摘要压缩",
        })
        return inject_context_after_compact(result, context_text), True

    # Strategy 4: full AI compaction with structured prompt
    try:
        result = await _ai_compact(
            messages, system_prompt, provider, model_name, temperature
        )
    except Exception as exc:
        logger.warning("Strategy 4 error: %s", exc)
        result = None

    if result is not None:
        _record_success(group_id)
        await interaction.broadcast(group_id, {
            "type": "compaction", "temp_id": temp_id,
            "strategy": "ai_full",
            "message": "上下文已通过 AI 全量摘要压缩（9 段结构化）",
        })
        return inject_context_after_compact(result, context_text), True

    _record_failure(group_id)
    return messages, False


# ---------------------------------------------------------------------------
# compact_conversation — direct compaction (overflow recovery & pre-run)
# ---------------------------------------------------------------------------

async def compact_conversation(
    messages: list[dict],
    system_prompt: str,
    provider: str,
    model_name: str,
    temperature: float,
    keep_recent: int = 6,
    context_text: str = "",
) -> list[dict]:
    """Immediately compact, keeping `keep_recent` tail messages verbatim.

    Used by overflow-recovery paths and pre-run compaction where the
    threshold check has already been done by the caller.
    Falls back to a truncation marker if AI fails.
    context_text: workspace context to re-inject into the summary message.
    """
    if len(messages) <= keep_recent:
        return messages

    split_idx = len(messages) - keep_recent
    # Walk left past tool messages to avoid splitting a tool-call group
    while 0 < split_idx < len(messages) and messages[split_idx].get("role") == "tool":
        split_idx -= 1

    if split_idx <= 0:
        return messages

    to_summarize = messages[:split_idx]
    recent = messages[split_idx:]

    result = await _ai_compact(to_summarize, system_prompt, provider, model_name, temperature)
    if result is not None:
        return inject_context_after_compact(result + recent, context_text)

    # AI failed — use a plain marker so the session can continue
    fallback = [{"role": "user", "content": f"（{len(to_summarize)} 条历史消息已截断）"}] + recent
    return inject_context_after_compact(fallback, context_text)


# ---------------------------------------------------------------------------
# maybe_compact_db_history — post-run DB-level compaction (background task)
# ---------------------------------------------------------------------------

async def maybe_compact_db_history(
    group_id: int,
    bot_id: int,
    provider: str,
    model: str,
    temperature: float,
    broadcaster,
) -> None:
    """Post-run: if group DB history exceeds token budget, summarise and soft-delete old messages.

    Uses the 9-section structured prompt via compact_conversation.
    Concurrency-safe via _db_compaction_locks.
    """
    if group_id in _db_compaction_locks:
        return
    _db_compaction_locks.add(group_id)
    try:
        from db import get_db, write_connect, get_messages, save_compaction_summary

        async with get_db() as db:
            all_msgs = await get_messages(db, group_id, limit=200)

        active = [m for m in all_msgs if not m.get("is_deleted")]
        total_tokens = estimate_tokens(active)
        if total_tokens <= _DB_COMPACTION_TOKEN_THRESHOLD:
            return

        to_keep = active[-_DB_COMPACTION_KEEP_RECENT:]
        to_summarize = active[:-_DB_COMPACTION_KEEP_RECENT]
        if not to_summarize:
            return

        keep_ids = {m["id"] for m in to_keep}

        # Convert DB messages to AI-format for compact_conversation
        ai_messages = [
            {
                "role": "assistant" if m.get("sender_type") == "bot" else "user",
                "content": (
                    f"[{m.get('sender_name', '?')}]: {(m.get('content') or '')[:1000]}"
                ),
            }
            for m in to_summarize
        ]

        compacted = await compact_conversation(
            ai_messages, "", provider, model, temperature, keep_recent=0
        )
        if not compacted:
            return

        first = compacted[0]
        raw_content = first.get("content", "")
        summary_text = (
            raw_content[len("【历史摘要】\n"):]
            if raw_content.startswith("【历史摘要】\n")
            else raw_content
        )
        if not summary_text:
            return

        async with write_connect() as db:
            summary_id = await save_compaction_summary(
                db, group_id, bot_id, summary_text, keep_ids
            )

        await interaction.broadcast(group_id, {
            "type": "db_compaction",
            "summary_id": summary_id,
            "deleted_count": len(to_summarize),
            "message": (
                f"DB 历史已压缩（{total_tokens:,} tokens），"
                f"{len(to_summarize)} 条旧消息归档"
            ),
        })
    except Exception as exc:
        logger.error("DB compaction error for group %s: %s", group_id, exc)
    finally:
        _db_compaction_locks.discard(group_id)
