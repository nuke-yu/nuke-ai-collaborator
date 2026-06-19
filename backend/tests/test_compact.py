"""
tests/test_compact.py — Unit tests for executors/compact.py

Covers every public function and all major branches:
  - Token helpers: estimate_tokens, autocompact_threshold, snip_threshold
  - Strategy 1: apply_tool_result_microcompact
  - Strategy 2: snip_if_needed + _count_conversation_pairs
  - format_compact_summary
  - Strategy 5 gate: should_use_cached_microcompact
  - Circuit breaker: _circuit_open, _record_failure, _record_success
  - Async strategy 3: _try_session_memory_compact
  - Async strategy 4: _ai_compact
  - Orchestrator: auto_compact_if_needed
  - Overflow recovery: compact_conversation
  - DB compaction: maybe_compact_db_history
"""
import sys
import os
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call as mock_call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executors.compact as compact
from executors.compact import (
    MC_KEEP_RECENT_TOOL_RESULTS,
    MC_CLEARED_MARKER,
    SNIP_KEEP_PAIRS,
    MAX_CONSECUTIVE_FAILURES,
    _MICROCOMPACT_TOOLS,
    _DEFAULT_CONTEXT_WINDOW,
    _MODEL_CONTEXT_WINDOWS,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    AUTOCOMPACT_BUFFER_TOKENS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_msg(name: str, content: str = "result", tool_call_id: str = "id1") -> dict:
    return {"role": "tool", "name": name, "tool_call_id": tool_call_id, "content": content}


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _asst(content: str) -> dict:
    return {"role": "assistant", "content": content}


def _make_messages(n_compactable_tools: int, add_non_compactable: bool = False) -> list:
    """Build a message list with n compactable tool messages and optional non-compactable ones."""
    msgs = []
    for i in range(n_compactable_tools):
        msgs.append(_tool_msg("run_shell", f"output {i}", f"id{i}"))
    if add_non_compactable:
        msgs.append(_tool_msg("unknown_tool", "result", "id_nc"))
    return msgs


def _make_pairs(n: int) -> list:
    """Build n user/assistant exchange pairs."""
    msgs = []
    for i in range(n):
        msgs.append(_user(f"question {i}"))
        msgs.append(_asst(f"answer {i}"))
    return msgs


# ---------------------------------------------------------------------------
# 1. Token helpers
# ---------------------------------------------------------------------------

class TestEstimateTokens(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(compact.estimate_tokens([]), 0)

    def test_scales_with_content_length(self):
        short = compact.estimate_tokens([_user("hi")])
        long = compact.estimate_tokens([_user("a" * 400)])
        self.assertGreater(long, short)
        # coarse heuristic ≈ chars / 4
        self.assertGreater(long, 80)
        self.assertLess(long, 130)

    def test_multiple_messages_accumulate(self):
        one = compact.estimate_tokens([_user("a" * 100)])
        two = compact.estimate_tokens([_user("a" * 100), _asst("b" * 100)])
        self.assertGreater(two, one)

    def test_list_content_serialised(self):
        msgs = [{"role": "assistant", "content": [{"type": "text", "text": "hello"}]}]
        result = compact.estimate_tokens(msgs)
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)

    def test_none_content_does_not_crash(self):
        msgs = [{"role": "tool", "content": None, "name": "x"}]
        result = compact.estimate_tokens(msgs)
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)

    def test_does_not_serialise_whole_array(self):
        """DFT-052: must not json.dumps the entire message array on each call."""
        msgs = [
            _user("a" * 1000), _asst("b" * 1000),
            {"role": "tool", "content": None, "name": "x"},
        ]
        with patch.object(
            compact.json, "dumps",
            side_effect=AssertionError("estimate_tokens must not json.dumps the full array"),
        ):
            result = compact.estimate_tokens(msgs)
        self.assertIsInstance(result, int)
        self.assertGreater(result, 400)


class TestEstimateTokensIncremental(unittest.TestCase):
    """DFT-030: incremental cache in estimate_tokens."""

    def setUp(self):
        compact._token_cache.clear()

    def test_cache_hit_on_second_call(self):
        msgs = [_user("hello"), _asst("world")]
        t1 = compact.estimate_tokens(msgs)
        self.assertIn(id(msgs), compact._token_cache)
        t2 = compact.estimate_tokens(msgs)
        self.assertEqual(t1, t2)

    def test_incremental_one_append_matches_full_recompute(self):
        msgs = [_user("hello")]
        compact.estimate_tokens(msgs)  # seed cache
        msgs.append(_asst("world " * 20))
        t_incr = compact.estimate_tokens(msgs)
        # full recompute on a different list object with the same content
        compact._token_cache.clear()
        t_full = compact.estimate_tokens(list(msgs))
        self.assertEqual(t_incr, t_full)

    def test_new_list_object_does_full_recompute(self):
        msgs1 = [_user("hello")]
        msgs2 = [_user("hello")]  # different object, same content
        t1 = compact.estimate_tokens(msgs1)
        t2 = compact.estimate_tokens(msgs2)
        self.assertEqual(t1, t2)
        self.assertIn(id(msgs1), compact._token_cache)
        self.assertIn(id(msgs2), compact._token_cache)


class TestInjectContextAfterCompact(unittest.TestCase):

    def test_empty_context_returns_unchanged(self):
        msgs = [_user("summary")]
        result = compact.inject_context_after_compact(msgs, "")
        self.assertEqual(result, msgs)

    def test_whitespace_context_returns_unchanged(self):
        msgs = [_user("summary")]
        result = compact.inject_context_after_compact(msgs, "   \n  ")
        self.assertEqual(result, msgs)

    def test_empty_messages_returns_unchanged(self):
        result = compact.inject_context_after_compact([], "ctx")
        self.assertEqual(result, [])

    def test_context_prepended_to_first_message_string(self):
        msgs = [_user("【历史摘要】\n..."), _user("hi")]
        result = compact.inject_context_after_compact(msgs, "=== AGENT.md ===\ncontent")
        self.assertEqual(len(result), 2)
        self.assertIn("工作区文件", result[0]["content"])
        self.assertIn("AGENT.md", result[0]["content"])
        self.assertIn("【历史摘要】", result[0]["content"])
        # tail messages unchanged
        self.assertEqual(result[1], _user("hi"))

    def test_does_not_mutate_input(self):
        msgs = [_user("summary")]
        original_content = msgs[0]["content"]
        compact.inject_context_after_compact(msgs, "ctx")
        self.assertEqual(msgs[0]["content"], original_content)

    def test_truncation_at_budget(self):
        long_ctx = "x" * 30_000
        msgs = [_user("summary")]
        result = compact.inject_context_after_compact(msgs, long_ctx, budget=25_000)
        content = result[0]["content"]
        self.assertIn("25,000 字符", content)
        # The actual snippet is at most budget chars + truncation note
        snippet_start = content.index("】\n") + 2
        raw_snippet = content[snippet_start:]
        self.assertLessEqual(len(raw_snippet), 25_000 + 200)  # budget + marker overhead

    def test_within_budget_no_truncation_marker(self):
        ctx = "=== AGENT.md ===\nshort"
        msgs = [_user("summary")]
        result = compact.inject_context_after_compact(msgs, ctx, budget=25_000)
        self.assertNotIn("字符]", result[0]["content"])

    def test_list_content_prepends_text_block(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "summary"}]}]
        result = compact.inject_context_after_compact(msgs, "ctx")
        content = result[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("工作区文件", content[0]["text"])
        self.assertEqual(content[1], {"type": "text", "text": "summary"})

    def test_returns_new_list_object(self):
        msgs = [_user("a"), _user("b")]
        result = compact.inject_context_after_compact(msgs, "ctx")
        self.assertIsNot(result, msgs)


class TestBuildFileTrackerXml(unittest.TestCase):

    def test_empty_tracker_returns_empty_string(self):
        self.assertEqual(compact.build_file_tracker_xml({}), "")

    def test_read_only(self):
        xml = compact.build_file_tracker_xml({"a.txt": "read"})
        self.assertIn("<file_operations>", xml)
        self.assertIn("<read>a.txt</read>", xml)
        self.assertNotIn("<modified>", xml)
        self.assertIn("</file_operations>", xml)

    def test_modified_only(self):
        xml = compact.build_file_tracker_xml({"b.py": "modified"})
        self.assertIn("<modified>b.py</modified>", xml)
        self.assertNotIn("<read>", xml)

    def test_read_and_modified_both_present(self):
        tracker = {"r.txt": "read", "w.txt": "modified"}
        xml = compact.build_file_tracker_xml(tracker)
        self.assertIn("<read>r.txt</read>", xml)
        self.assertIn("<modified>w.txt</modified>", xml)

    def test_modified_appears_after_read(self):
        tracker = {"r.txt": "read", "w.txt": "modified"}
        xml = compact.build_file_tracker_xml(tracker)
        read_pos     = xml.index("<read>")
        modified_pos = xml.index("<modified>")
        self.assertLess(read_pos, modified_pos)

    def test_paths_sorted(self):
        tracker = {"z.txt": "read", "a.txt": "read", "m.txt": "read"}
        xml = compact.build_file_tracker_xml(tracker)
        a_pos = xml.index("a.txt")
        m_pos = xml.index("m.txt")
        z_pos = xml.index("z.txt")
        self.assertLess(a_pos, m_pos)
        self.assertLess(m_pos, z_pos)

    def test_modified_overrides_read_in_caller(self):
        # Tracker semantics: write after read → "modified" (caller responsibility, tracked here)
        tracker = {"f.txt": "modified"}  # was read first, caller upgraded it
        xml = compact.build_file_tracker_xml(tracker)
        self.assertIn("<modified>f.txt</modified>", xml)
        self.assertNotIn("<read>f.txt</read>", xml)

    def test_multiple_files_each_on_own_line(self):
        tracker = {"a.txt": "read", "b.txt": "read", "c.txt": "modified"}
        xml = compact.build_file_tracker_xml(tracker)
        lines = xml.splitlines()
        read_lines = [l for l in lines if "<read>" in l]
        mod_lines  = [l for l in lines if "<modified>" in l]
        self.assertEqual(len(read_lines), 2)
        self.assertEqual(len(mod_lines), 1)

    def test_inject_context_includes_file_tracker(self):
        """inject_context_after_compact with file tracker XML in context_text re-injects both."""
        tracker = {"notes.md": "read", "output.py": "modified"}
        ft_xml = compact.build_file_tracker_xml(tracker)
        combined = f"=== AGENT.md ===\ncontent\n\n{ft_xml}"
        msgs = [_user("【历史摘要】\n...")]
        result = compact.inject_context_after_compact(msgs, combined)
        content = result[0]["content"]
        self.assertIn("AGENT.md", content)
        self.assertIn("<read>notes.md</read>", content)
        self.assertIn("<modified>output.py</modified>", content)


class TestThresholds(unittest.TestCase):

    def test_autocompact_threshold_known_model(self):
        window = _MODEL_CONTEXT_WINDOWS["deepseek-chat"]  # 64_000
        expected = window - MAX_OUTPUT_TOKENS_FOR_SUMMARY - AUTOCOMPACT_BUFFER_TOKENS
        self.assertEqual(compact.autocompact_threshold("deepseek-chat"), expected)

    def test_autocompact_threshold_claude(self):
        window = _MODEL_CONTEXT_WINDOWS["claude-opus-4-7"]  # 200_000
        expected = window - MAX_OUTPUT_TOKENS_FOR_SUMMARY - AUTOCOMPACT_BUFFER_TOKENS
        self.assertEqual(compact.autocompact_threshold("claude-opus-4-7"), expected)

    def test_autocompact_threshold_unknown_model(self):
        expected = _DEFAULT_CONTEXT_WINDOW - MAX_OUTPUT_TOKENS_FOR_SUMMARY - AUTOCOMPACT_BUFFER_TOKENS
        self.assertEqual(compact.autocompact_threshold("unknown-model"), expected)

    def test_snip_threshold_is_70_percent(self):
        window = _MODEL_CONTEXT_WINDOWS["gpt-4o"]  # 128_000
        self.assertEqual(compact.snip_threshold("gpt-4o"), int(128_000 * 0.70))

    def test_snip_threshold_unknown_model(self):
        self.assertEqual(compact.snip_threshold("???"), int(_DEFAULT_CONTEXT_WINDOW * 0.70))


# ---------------------------------------------------------------------------
# 2. Strategy 1: apply_tool_result_microcompact
# ---------------------------------------------------------------------------

class TestApplyToolResultMicrocompact(unittest.TestCase):

    def test_empty_list_returns_empty(self):
        self.assertEqual(compact.apply_tool_result_microcompact([]), [])

    def test_no_tool_messages_unchanged(self):
        msgs = [_user("hi"), _asst("hello")]
        result = compact.apply_tool_result_microcompact(msgs)
        self.assertEqual(result, msgs)

    def test_fewer_than_keep_not_cleared(self):
        """If compactable tools ≤ MC_KEEP_RECENT_TOOL_RESULTS, nothing is cleared."""
        msgs = _make_messages(MC_KEEP_RECENT_TOOL_RESULTS)
        result = compact.apply_tool_result_microcompact(msgs)
        self.assertEqual(result, msgs)

    def test_exactly_keep_not_cleared(self):
        msgs = _make_messages(MC_KEEP_RECENT_TOOL_RESULTS)
        result = compact.apply_tool_result_microcompact(msgs)
        for m in result:
            self.assertNotEqual(m["content"], MC_CLEARED_MARKER)

    def test_one_over_keep_clears_oldest(self):
        """With MC_KEEP_RECENT_TOOL_RESULTS+1 tools, the first one is cleared."""
        msgs = _make_messages(MC_KEEP_RECENT_TOOL_RESULTS + 1)
        result = compact.apply_tool_result_microcompact(msgs)
        self.assertEqual(result[0]["content"], MC_CLEARED_MARKER)
        for m in result[1:]:
            self.assertNotEqual(m["content"], MC_CLEARED_MARKER)

    def test_many_over_keep_clears_all_but_recent(self):
        total = MC_KEEP_RECENT_TOOL_RESULTS + 3
        msgs = _make_messages(total)
        result = compact.apply_tool_result_microcompact(msgs)
        for m in result[:3]:
            self.assertEqual(m["content"], MC_CLEARED_MARKER)
        for m in result[3:]:
            self.assertNotEqual(m["content"], MC_CLEARED_MARKER)

    def test_non_microcompact_tool_not_cleared(self):
        """Tools NOT in _MICROCOMPACT_TOOLS must not be cleared."""
        compactable = [_tool_msg("run_shell", "res", f"id{i}") for i in range(MC_KEEP_RECENT_TOOL_RESULTS + 2)]
        non_compactable = [_tool_msg("unknown_tool", "keep_me", "idX")]
        msgs = compactable + non_compactable
        result = compact.apply_tool_result_microcompact(msgs)
        nc = next(m for m in result if m.get("name") == "unknown_tool")
        self.assertEqual(nc["content"], "keep_me")

    def test_already_cleared_marker_stays_unchanged(self):
        """A message already containing the cleared marker should not be double-processed."""
        msgs = [
            _tool_msg("run_shell", MC_CLEARED_MARKER, "id0"),
        ] + [_tool_msg("run_shell", f"fresh {i}", f"id{i+1}") for i in range(MC_KEEP_RECENT_TOOL_RESULTS + 1)]
        result = compact.apply_tool_result_microcompact(msgs)
        # First message is already cleared, stays cleared
        self.assertEqual(result[0]["content"], MC_CLEARED_MARKER)

    def test_preserves_message_structure(self):
        """Cleared messages must retain role, name, tool_call_id."""
        msgs = _make_messages(MC_KEEP_RECENT_TOOL_RESULTS + 1)
        result = compact.apply_tool_result_microcompact(msgs)
        cleared = result[0]
        self.assertEqual(cleared["role"], "tool")
        self.assertIn("name", cleared)
        self.assertIn("tool_call_id", cleared)

    def test_does_not_mutate_input(self):
        msgs = _make_messages(MC_KEEP_RECENT_TOOL_RESULTS + 2)
        original_contents = [m["content"] for m in msgs]
        compact.apply_tool_result_microcompact(msgs)
        for m, orig in zip(msgs, original_contents):
            self.assertEqual(m["content"], orig)

    def test_non_tool_messages_between_tools_preserved(self):
        msgs = [
            _user("q"),
            _tool_msg("run_shell", "result", "id0"),
            _asst("done"),
        ]
        result = compact.apply_tool_result_microcompact(msgs)
        self.assertEqual(result[0], msgs[0])
        self.assertEqual(result[2], msgs[2])

    def test_all_microcompact_tool_names(self):
        """Verify every tool in _MICROCOMPACT_TOOLS can be cleared."""
        for tool_name in _MICROCOMPACT_TOOLS:
            msgs = [_tool_msg(tool_name, "old", f"id{i}") for i in range(MC_KEEP_RECENT_TOOL_RESULTS + 1)]
            result = compact.apply_tool_result_microcompact(msgs)
            self.assertEqual(result[0]["content"], MC_CLEARED_MARKER,
                             f"Tool '{tool_name}' should be cleared but wasn't")


# ---------------------------------------------------------------------------
# 3. Strategy 2: snip_if_needed
# ---------------------------------------------------------------------------

class TestSnipIfNeeded(unittest.TestCase):

    def _over_threshold_messages(self, model="deepseek-chat") -> list:
        """Build messages with token count over snip_threshold."""
        threshold = compact.snip_threshold(model)
        # Make one very long message pair to go over threshold
        big_content = "x" * (threshold * 4 + 100)
        return _make_pairs(SNIP_KEEP_PAIRS + 1) + [_user(big_content)]

    def test_under_threshold_unchanged(self):
        msgs = _make_pairs(2)
        result, freed = compact.snip_if_needed(msgs, "deepseek-chat")
        self.assertEqual(result, msgs)
        self.assertEqual(freed, 0)

    def test_too_few_pairs_no_snip(self):
        """When pairs ≤ SNIP_KEEP_PAIRS, never snip even if over threshold."""
        threshold = compact.snip_threshold("deepseek-chat")
        big = "x" * (threshold * 4 + 100)
        msgs = [_user(big), _asst("short")]  # 1 pair = SNIP_KEEP_PAIRS (1 < 4? depends on constant)
        # Make pairs exactly at SNIP_KEEP_PAIRS
        msgs = _make_pairs(SNIP_KEEP_PAIRS) + [_user("x" * (threshold * 4 + 100))]
        result, freed = compact.snip_if_needed(msgs, "deepseek-chat")
        # SNIP_KEEP_PAIRS pairs remain — no snipping because we'd go below minimum
        self.assertEqual(result, msgs)
        self.assertEqual(freed, 0)

    def test_removes_oldest_pair_when_over(self):
        threshold = compact.snip_threshold("deepseek-chat")
        big = "x" * (threshold * 4 + 100)
        # SNIP_KEEP_PAIRS + 1 pairs, then a big message to go over threshold
        msgs = _make_pairs(SNIP_KEEP_PAIRS + 1) + [_user(big)]
        result, freed = compact.snip_if_needed(msgs, "deepseek-chat")
        # First pair (msgs[0], msgs[1]) should be removed
        self.assertNotIn(msgs[0], result)
        self.assertNotIn(msgs[1], result)
        self.assertIn(msgs[2], result)  # second pair preserved
        self.assertGreater(freed, 0)

    def test_tool_messages_between_pair_blocks_snip(self):
        """A tool message between user and assistant prevents snipping that pair."""
        threshold = compact.snip_threshold("deepseek-chat")
        big = "x" * (threshold * 4 + 100)
        msgs = [
            _user("question"),
            _tool_msg("run_shell", "output"),  # tool between user and asst
            _asst("answer"),
        ] + _make_pairs(SNIP_KEEP_PAIRS) + [_user(big)]
        result, freed = compact.snip_if_needed(msgs, "deepseek-chat")
        # First user message has tool between it and assistant — blocked
        self.assertIn(msgs[0], result)
        self.assertEqual(freed, 0)

    def test_no_user_messages_no_snip(self):
        msgs = [_asst("just an answer")]
        result, freed = compact.snip_if_needed(msgs, "deepseek-chat")
        self.assertEqual(result, msgs)
        self.assertEqual(freed, 0)

    def test_no_assistant_after_user_no_snip(self):
        msgs = [_user("a question with no answer")]
        result, freed = compact.snip_if_needed(msgs, "deepseek-chat")
        self.assertEqual(result, msgs)
        self.assertEqual(freed, 0)

    def test_freed_tokens_correct(self):
        """freed should equal estimate_tokens of the removed messages."""
        threshold = compact.snip_threshold("deepseek-chat")
        pair0 = [_user("first question"), _asst("first answer")]
        pair1_n = _make_pairs(SNIP_KEEP_PAIRS)
        big = [_user("x" * (threshold * 4 + 100))]
        msgs = pair0 + pair1_n + big

        result, freed = compact.snip_if_needed(msgs, "deepseek-chat")
        expected_freed = compact.estimate_tokens(pair0)
        self.assertEqual(freed, expected_freed)


class TestCountConversationPairs(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(compact._count_conversation_pairs([]), 0)

    def test_single_user(self):
        self.assertEqual(compact._count_conversation_pairs([_user("hi")]), 0)

    def test_one_pair(self):
        self.assertEqual(compact._count_conversation_pairs([_user("hi"), _asst("hello")]), 1)

    def test_two_pairs(self):
        msgs = _make_pairs(2)
        self.assertEqual(compact._count_conversation_pairs(msgs), 2)

    def test_tool_messages_ignored_in_count(self):
        msgs = [_user("q"), _tool_msg("run_shell"), _asst("a")]
        self.assertEqual(compact._count_conversation_pairs(msgs), 1)


# ---------------------------------------------------------------------------
# 4. format_compact_summary
# ---------------------------------------------------------------------------

class TestFormatCompactSummary(unittest.TestCase):

    def test_extracts_summary_block(self):
        raw = "<analysis>scratch</analysis>\n<summary>key points</summary>"
        self.assertEqual(compact.format_compact_summary(raw), "key points")

    def test_strips_analysis_block(self):
        raw = "<analysis>thinking...</analysis><summary>result</summary>"
        result = compact.format_compact_summary(raw)
        self.assertNotIn("analysis", result)
        self.assertNotIn("thinking", result)

    def test_multiline_summary(self):
        raw = "<analysis>x</analysis>\n<summary>\nLine 1\nLine 2\n</summary>"
        result = compact.format_compact_summary(raw)
        self.assertIn("Line 1", result)
        self.assertIn("Line 2", result)

    def test_no_tags_fallback_returns_full_text(self):
        raw = "No tags here, just text."
        self.assertEqual(compact.format_compact_summary(raw), raw)

    def test_only_analysis_no_summary_returns_stripped_text(self):
        raw = "<analysis>scratch work</analysis>\nRemainder text"
        result = compact.format_compact_summary(raw)
        self.assertNotIn("scratch work", result)
        self.assertIn("Remainder text", result)

    def test_empty_string(self):
        self.assertEqual(compact.format_compact_summary(""), "")

    def test_nested_content_not_truncated(self):
        long_content = "detail " * 100
        raw = f"<analysis>a</analysis>\n<summary>{long_content}</summary>"
        result = compact.format_compact_summary(raw)
        self.assertEqual(result.strip(), long_content.strip())


# ---------------------------------------------------------------------------
# 5. Strategy 5 gate: should_use_cached_microcompact
# ---------------------------------------------------------------------------

class TestShouldUseCachedMicrocompact(unittest.TestCase):

    def test_claude_returns_true(self):
        self.assertTrue(compact.should_use_cached_microcompact("claude"))

    def test_deepseek_returns_false(self):
        self.assertFalse(compact.should_use_cached_microcompact("deepseek"))

    def test_openai_returns_false(self):
        self.assertFalse(compact.should_use_cached_microcompact("openai"))

    def test_ollama_returns_false(self):
        self.assertFalse(compact.should_use_cached_microcompact("ollama"))

    def test_qwen_returns_false(self):
        self.assertFalse(compact.should_use_cached_microcompact("qwen"))

    def test_empty_string_returns_false(self):
        self.assertFalse(compact.should_use_cached_microcompact(""))


# ---------------------------------------------------------------------------
# 6. Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker(unittest.TestCase):

    def setUp(self):
        compact._compaction_failures.clear()

    def tearDown(self):
        compact._compaction_failures.clear()

    def test_fresh_state_not_open(self):
        self.assertFalse(compact._circuit_open(group_id=1))

    def test_below_max_not_open(self):
        for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
            compact._record_failure(group_id=1)
        self.assertFalse(compact._circuit_open(group_id=1))

    def test_at_max_open(self):
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            compact._record_failure(group_id=1)
        self.assertTrue(compact._circuit_open(group_id=1))

    def test_beyond_max_still_open(self):
        for _ in range(MAX_CONSECUTIVE_FAILURES + 5):
            compact._record_failure(group_id=1)
        self.assertTrue(compact._circuit_open(group_id=1))

    def test_success_resets_failures(self):
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            compact._record_failure(group_id=1)
        self.assertTrue(compact._circuit_open(group_id=1))
        compact._record_success(group_id=1)
        self.assertFalse(compact._circuit_open(group_id=1))

    def test_independent_groups(self):
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            compact._record_failure(group_id=1)
        self.assertTrue(compact._circuit_open(group_id=1))
        self.assertFalse(compact._circuit_open(group_id=2))

    def test_success_on_never_failed_group_is_noop(self):
        compact._record_success(group_id=99)
        self.assertFalse(compact._circuit_open(group_id=99))


# ---------------------------------------------------------------------------
# 7. Async: _try_session_memory_compact
# ---------------------------------------------------------------------------

class TestTrySessionMemoryCompact(unittest.IsolatedAsyncioTestCase):

    async def test_empty_messages_returns_none(self):
        result = await compact._try_session_memory_compact([], "sp", "deepseek", "deepseek-chat", 0.7)
        self.assertIsNone(result)

    async def test_first_message_not_summary_returns_none(self):
        msgs = [_user("regular message"), _asst("answer")]
        result = await compact._try_session_memory_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)
        self.assertIsNone(result)

    async def test_delta_under_threshold_returns_none(self):
        """Summary exists but delta is small — no recompaction needed."""
        msgs = [
            _user("【历史摘要】\nprevious summary"),
            _user("small delta"),
            _asst("small answer"),
        ]
        # delta (msgs[1:]) will be tiny, well under autocompact_threshold
        result = await compact._try_session_memory_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)
        self.assertIsNone(result)

    async def test_delta_over_threshold_calls_ai_and_combines(self):
        """When delta is over threshold, AI is called and summary is combined."""
        threshold = compact.autocompact_threshold("deepseek-chat")
        big_delta = "x" * (threshold * 4 + 100)
        msgs = [
            _user("【历史摘要】\nbase summary content"),
            _user(big_delta),
            _asst("answer"),
        ]
        mock_ai_result = {"type": "text", "content": "delta summary"}
        with patch("executors.compact.call_ai_once", new=AsyncMock(return_value=mock_ai_result)):
            result = await compact._try_session_memory_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        combined = result[0]["content"]
        self.assertIn("base summary content", combined)
        self.assertIn("delta summary", combined)

    async def test_ai_failure_returns_none(self):
        """If the AI call fails, returns None gracefully."""
        threshold = compact.autocompact_threshold("deepseek-chat")
        big_delta = "x" * (threshold * 4 + 100)
        msgs = [
            _user("【历史摘要】\nbase"),
            _user(big_delta),
        ]
        with patch("executors.compact.call_ai_once", new=AsyncMock(side_effect=Exception("AI down"))):
            result = await compact._try_session_memory_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)
        self.assertIsNone(result)

    async def test_ai_empty_response_returns_none(self):
        threshold = compact.autocompact_threshold("deepseek-chat")
        big_delta = "x" * (threshold * 4 + 100)
        msgs = [_user("【历史摘要】\nbase"), _user(big_delta)]
        with patch("executors.compact.call_ai_once", new=AsyncMock(return_value={"type": "text", "content": ""})):
            result = await compact._try_session_memory_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 8. Async: _ai_compact
# ---------------------------------------------------------------------------

class TestAiCompact(unittest.IsolatedAsyncioTestCase):

    async def test_returns_formatted_summary_message(self):
        raw_ai = "<analysis>thinking</analysis>\n<summary>key decisions here</summary>"
        with patch("executors.compact.call_ai_once", new=AsyncMock(return_value={"type": "text", "content": raw_ai})):
            result = await compact._ai_compact(
                [_user("hello"), _asst("hi")], "system", "deepseek", "deepseek-chat", 0.7
            )
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "user")
        self.assertTrue(result[0]["content"].startswith("【历史摘要】"))
        self.assertIn("key decisions here", result[0]["content"])
        self.assertNotIn("<analysis>", result[0]["content"])

    async def test_ai_exception_returns_none(self):
        with patch("executors.compact.call_ai_once", new=AsyncMock(side_effect=Exception("timeout"))):
            result = await compact._ai_compact([_user("q")], "sp", "deepseek", "deepseek-chat", 0.7)
        self.assertIsNone(result)

    async def test_empty_ai_response_returns_none(self):
        with patch("executors.compact.call_ai_once", new=AsyncMock(return_value={"type": "text", "content": ""})):
            result = await compact._ai_compact([_user("q")], "sp", "deepseek", "deepseek-chat", 0.7)
        self.assertIsNone(result)

    async def test_tool_messages_included_in_history(self):
        """Tool messages must appear in the history text sent to AI."""
        captured_messages = []

        async def capture(*args, **kwargs):
            captured_messages.extend(args[1])  # args[1] is the messages list
            return {"type": "text", "content": "<summary>s</summary>"}

        msgs = [_user("q"), _tool_msg("run_shell", "tool output"), _asst("a")]
        with patch("executors.compact.call_ai_once", new=capture):
            await compact._ai_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)

        # The history_text in the user message should reference tool output
        user_content = captured_messages[0]["content"]
        self.assertIn("tool output", user_content)

    async def test_uses_max_output_tokens_for_summary(self):
        """AI compaction must use MAX_OUTPUT_TOKENS_FOR_SUMMARY as max_tokens."""
        captured_kwargs = {}

        async def capture(system, messages, provider, model, temperature, max_tokens):
            captured_kwargs["max_tokens"] = max_tokens
            return {"type": "text", "content": "<summary>s</summary>"}

        with patch("executors.compact.call_ai_once", new=capture):
            await compact._ai_compact([_user("q")], "sp", "deepseek", "deepseek-chat", 0.7)

        self.assertEqual(captured_kwargs["max_tokens"], compact.MAX_OUTPUT_TOKENS_FOR_SUMMARY)

    async def test_ptl_retry_drops_oldest_then_succeeds(self):
        """On PROMPT_TOO_LONG, drop oldest rounds and retry; a shorter payload succeeds."""
        from ai.client import AIContextOverflowError

        msgs = _make_pairs(10)               # 20 messages
        sent_lengths = []

        async def flaky(system, messages, provider, model, temperature, max_tokens):
            # First call overflows; record what reached the model each time.
            sent_lengths.append(len(messages[0]["content"]))
            if len(sent_lengths) == 1:
                raise AIContextOverflowError("prompt is too long")
            return {"type": "text", "content": "<summary>ok</summary>"}

        with patch("executors.compact.call_ai_once", new=flaky):
            result = await compact._ai_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)

        self.assertIsNotNone(result)
        self.assertIn("ok", result[0]["content"])
        self.assertEqual(len(sent_lengths), 2)            # one retry
        self.assertLess(sent_lengths[1], sent_lengths[0])  # retry payload is shorter

    async def test_ptl_retry_notes_dropped_history(self):
        """The retried prompt carries a note that older history was dropped."""
        from ai.client import AIContextOverflowError

        msgs = _make_pairs(10)
        seen = []

        async def flaky(system, messages, provider, model, temperature, max_tokens):
            seen.append(messages[0]["content"])
            if len(seen) == 1:
                raise AIContextOverflowError("context_length_exceeded")
            return {"type": "text", "content": "<summary>ok</summary>"}

        with patch("executors.compact.call_ai_once", new=flaky):
            await compact._ai_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)

        self.assertNotIn("已省略", seen[0])   # first attempt: full history, no note
        self.assertIn("已省略", seen[1])      # retry: note added

    async def test_ptl_persistent_overflow_gives_up_returns_none(self):
        """If every attempt overflows, return None instead of looping forever."""
        from ai.client import AIContextOverflowError

        msgs = _make_pairs(10)
        calls = 0

        async def always_overflow(system, messages, provider, model, temperature, max_tokens):
            nonlocal calls
            calls += 1
            raise AIContextOverflowError("prompt is too long")

        with patch("executors.compact.call_ai_once", new=always_overflow):
            result = await compact._ai_compact(msgs, "sp", "deepseek", "deepseek-chat", 0.7)

        self.assertIsNone(result)
        # Bounded: at most the initial attempt + _PTL_MAX_RETRIES, and stops early
        # once _drop_oldest_rounds can no longer shrink the list.
        self.assertLessEqual(calls, compact._PTL_MAX_RETRIES + 1)
        self.assertGreaterEqual(calls, 2)

    def test_drop_oldest_rounds_shrinks(self):
        msgs = _make_pairs(5)                 # 10 messages
        shorter = compact._drop_oldest_rounds(msgs)
        self.assertLess(len(shorter), len(msgs))
        self.assertEqual(shorter, msgs[len(msgs) - len(shorter):])  # tail preserved

    def test_drop_oldest_rounds_cannot_shrink_singleton(self):
        one = [_user("only")]
        self.assertEqual(compact._drop_oldest_rounds(one), one)


# ---------------------------------------------------------------------------
# 9. Orchestrator: auto_compact_if_needed
# ---------------------------------------------------------------------------

class TestAutoCompactIfNeeded(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        compact._compaction_failures.clear()

    def tearDown(self):
        compact._compaction_failures.clear()

    def _over_threshold_msgs(self, model="deepseek-chat") -> list:
        threshold = compact.autocompact_threshold(model)
        return [_user("x" * (threshold * 4 + 1000))]

    async def test_under_threshold_returns_unchanged(self):
        msgs = [_user("short")]
        broadcaster = AsyncMock()
        result, compacted = await compact.auto_compact_if_needed(
            msgs, "deepseek-chat", 1, "sp", "deepseek", 0.7, broadcaster, "tid", 42
        )
        self.assertFalse(compacted)
        self.assertEqual(result, msgs)
        broadcaster.broadcast.assert_not_called()

    async def test_circuit_open_returns_unchanged(self):
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            compact._record_failure(group_id=1)
        msgs = self._over_threshold_msgs()
        broadcaster = AsyncMock()
        result, compacted = await compact.auto_compact_if_needed(
            msgs, "deepseek-chat", 1, "sp", "deepseek", 0.7, broadcaster, "tid", 42
        )
        self.assertFalse(compacted)
        self.assertEqual(result, msgs)

    async def test_strategy3_succeeds_no_strategy4(self):
        """When session-memory compact succeeds, strategy 4 is not attempted."""
        msgs = self._over_threshold_msgs()
        compacted_msgs = [_user("【历史摘要】\ncompact")]
        broadcaster = AsyncMock()
        with patch.object(compact, "_try_session_memory_compact", new=AsyncMock(return_value=compacted_msgs)), \
             patch.object(compact, "_ai_compact", new=AsyncMock()) as mock_ai:
            result, was_compacted = await compact.auto_compact_if_needed(
                msgs, "deepseek-chat", 1, "sp", "deepseek", 0.7, broadcaster, "tid", 42
            )
        self.assertTrue(was_compacted)
        self.assertEqual(result, compacted_msgs)
        mock_ai.assert_not_called()
        # Should broadcast with strategy="session_memory"
        broadcast_calls = broadcaster.broadcast.call_args_list
        self.assertTrue(any(
            c.args[1].get("strategy") == "session_memory" for c in broadcast_calls
        ))

    async def test_strategy3_fails_falls_to_strategy4(self):
        msgs = self._over_threshold_msgs()
        compacted_msgs = [_user("【历史摘要】\nfull compact")]
        broadcaster = AsyncMock()
        with patch.object(compact, "_try_session_memory_compact", new=AsyncMock(return_value=None)), \
             patch.object(compact, "_ai_compact", new=AsyncMock(return_value=compacted_msgs)):
            result, was_compacted = await compact.auto_compact_if_needed(
                msgs, "deepseek-chat", 1, "sp", "deepseek", 0.7, broadcaster, "tid", 42
            )
        self.assertTrue(was_compacted)
        self.assertEqual(result, compacted_msgs)
        broadcast_calls = broadcaster.broadcast.call_args_list
        self.assertTrue(any(
            c.args[1].get("strategy") == "ai_full" for c in broadcast_calls
        ))

    async def test_both_strategies_fail_records_failure(self):
        msgs = self._over_threshold_msgs()
        broadcaster = AsyncMock()
        with patch.object(compact, "_try_session_memory_compact", new=AsyncMock(return_value=None)), \
             patch.object(compact, "_ai_compact", new=AsyncMock(return_value=None)):
            result, was_compacted = await compact.auto_compact_if_needed(
                msgs, "deepseek-chat", 1, "sp", "deepseek", 0.7, broadcaster, "tid", 42
            )
        self.assertFalse(was_compacted)
        self.assertEqual(result, msgs)
        self.assertEqual(compact._compaction_failures.get(1, 0), 1)

    async def test_success_clears_failure_count(self):
        compact._compaction_failures[1] = MAX_CONSECUTIVE_FAILURES - 1
        msgs = self._over_threshold_msgs()
        compacted_msgs = [_user("【历史摘要】\nok")]
        broadcaster = AsyncMock()
        with patch.object(compact, "_try_session_memory_compact", new=AsyncMock(return_value=compacted_msgs)):
            await compact.auto_compact_if_needed(
                msgs, "deepseek-chat", 1, "sp", "deepseek", 0.7, broadcaster, "tid", 42
            )
        self.assertNotIn(1, compact._compaction_failures)


# ---------------------------------------------------------------------------
# 10. compact_conversation (overflow recovery)
# ---------------------------------------------------------------------------

class TestCompactConversation(unittest.IsolatedAsyncioTestCase):

    async def test_short_messages_unchanged(self):
        msgs = [_user("a"), _asst("b")]
        result = await compact.compact_conversation(msgs, "sp", "deepseek", "deepseek-chat", 0.7, keep_recent=6)
        self.assertEqual(result, msgs)

    async def test_ai_success_returns_summary_plus_recent(self):
        msgs = _make_pairs(10)
        summary_msg = [_user("【历史摘要】\nsummary")]

        with patch.object(compact, "_ai_compact", new=AsyncMock(return_value=summary_msg)):
            result = await compact.compact_conversation(
                msgs, "sp", "deepseek", "deepseek-chat", 0.7, keep_recent=4
            )
        # Should be: summary + last 4 messages
        self.assertEqual(result[0], summary_msg[0])
        self.assertEqual(result[1:], msgs[-4:])

    async def test_ai_failure_returns_marker_plus_recent(self):
        msgs = _make_pairs(10)
        with patch.object(compact, "_ai_compact", new=AsyncMock(return_value=None)):
            result = await compact.compact_conversation(
                msgs, "sp", "deepseek", "deepseek-chat", 0.7, keep_recent=4
            )
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("已截断", result[0]["content"])
        self.assertEqual(result[1:], msgs[-4:])

    async def test_walks_left_past_tool_messages(self):
        """Split point must walk left past tool messages to avoid orphaned tool_result."""
        msgs = _make_pairs(4) + [
            _asst("calling"),
            _tool_msg("run_shell", "output", "id_tool"),
        ]
        with patch.object(compact, "_ai_compact", new=AsyncMock(return_value=[_user("【历史摘要】\nok")])):
            result = await compact.compact_conversation(
                msgs, "sp", "deepseek", "deepseek-chat", 0.7, keep_recent=2
            )
        # Tool message should end up in the recent portion
        contents = [m.get("content") for m in result]
        self.assertIn("output", contents)

    async def test_keep_recent_zero_summarizes_all(self):
        msgs = _make_pairs(5)
        summary_msg = [_user("【历史摘要】\nall")]
        with patch.object(compact, "_ai_compact", new=AsyncMock(return_value=summary_msg)):
            result = await compact.compact_conversation(
                msgs, "sp", "deepseek", "deepseek-chat", 0.7, keep_recent=0
            )
        self.assertEqual(result, summary_msg)


# ---------------------------------------------------------------------------
# 11. maybe_compact_db_history
# ---------------------------------------------------------------------------

class TestMaybeCompactDbHistory(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        compact._db_compaction_locks.clear()

    def tearDown(self):
        compact._db_compaction_locks.clear()

    def _make_db_messages(self, n: int, chars_each: int = 100) -> list:
        return [
            {
                "id": i,
                "sender_name": "user",
                "sender_type": "human",
                "content": "x" * chars_each,
                "is_deleted": False,
            }
            for i in range(n)
        ]

    def _make_mock_db_cm(self, mock_db=None):
        """Return an async context manager mock that yields mock_db."""
        if mock_db is None:
            mock_db = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_db)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def test_skips_when_lock_held(self):
        compact._db_compaction_locks.add(1)
        broadcaster = AsyncMock()
        with patch("db.get_db") as mock_get_db:
            await compact.maybe_compact_db_history(1, 42, "deepseek", "deepseek-chat", 0.7, broadcaster)
        mock_get_db.assert_not_called()
        broadcaster.broadcast.assert_not_called()

    async def test_lock_released_after_run(self):
        """Lock must be cleaned up even when under threshold."""
        broadcaster = AsyncMock()
        # Under-threshold messages
        msgs = self._make_db_messages(5, chars_each=10)
        mock_db = MagicMock()
        with patch("db.get_db", return_value=self._make_mock_db_cm(mock_db)), \
             patch("db.get_messages", new=AsyncMock(return_value=msgs)):
            await compact.maybe_compact_db_history(1, 42, "deepseek", "deepseek-chat", 0.7, broadcaster)
        self.assertNotIn(1, compact._db_compaction_locks)

    async def test_under_threshold_no_action(self):
        threshold = compact._DB_COMPACTION_TOKEN_THRESHOLD
        # Each message is 10 chars → 2 tokens; need many to exceed threshold
        # With 5 messages × 10 chars, total tokens ≈ 12 << 30_000
        msgs = self._make_db_messages(5, chars_each=10)
        broadcaster = AsyncMock()
        mock_db = MagicMock()
        with patch("db.get_db", return_value=self._make_mock_db_cm(mock_db)), \
             patch("db.get_messages", new=AsyncMock(return_value=msgs)), \
             patch("db.save_compaction_summary", new=AsyncMock()) as mock_save:
            await compact.maybe_compact_db_history(1, 42, "deepseek", "deepseek-chat", 0.7, broadcaster)
        mock_save.assert_not_called()
        broadcaster.broadcast.assert_not_called()

    async def test_over_threshold_saves_summary_and_broadcasts(self):
        threshold = compact._DB_COMPACTION_TOKEN_THRESHOLD
        keep = compact._DB_COMPACTION_KEEP_RECENT
        # Need keep+1 messages so to_summarize is non-empty after slicing off `keep` messages,
        # AND total tokens must exceed threshold.
        chars_each = threshold * 4 // (keep + 1) + 100
        msgs = self._make_db_messages(keep + 3, chars_each=chars_each)
        broadcaster = AsyncMock()
        mock_db = MagicMock()
        summary_id = 999

        with patch("db.get_db", return_value=self._make_mock_db_cm(mock_db)), \
             patch("db.get_messages", new=AsyncMock(return_value=msgs)), \
             patch("db.save_compaction_summary", new=AsyncMock(return_value=summary_id)) as mock_save, \
             patch.object(compact, "compact_conversation",
                          new=AsyncMock(return_value=[_user("【历史摘要】\nsummary text")])):
            await compact.maybe_compact_db_history(1, 42, "deepseek", "deepseek-chat", 0.7, broadcaster)

        mock_save.assert_called_once()
        broadcaster.broadcast.assert_called_once()
        broadcast_msg = broadcaster.broadcast.call_args.args[1]
        self.assertEqual(broadcast_msg["type"], "db_compaction")
        self.assertEqual(broadcast_msg["summary_id"], summary_id)

    async def test_filters_deleted_messages(self):
        """Deleted messages must not count toward the token budget."""
        threshold = compact._DB_COMPACTION_TOKEN_THRESHOLD
        chars_each = threshold * 4 // 5 + 100
        msgs = self._make_db_messages(6, chars_each=chars_each)
        # Mark all as deleted
        for m in msgs:
            m["is_deleted"] = True
        broadcaster = AsyncMock()
        mock_db = MagicMock()
        with patch("db.get_db", return_value=self._make_mock_db_cm(mock_db)), \
             patch("db.get_messages", new=AsyncMock(return_value=msgs)), \
             patch("db.save_compaction_summary", new=AsyncMock()) as mock_save:
            await compact.maybe_compact_db_history(1, 42, "deepseek", "deepseek-chat", 0.7, broadcaster)
        mock_save.assert_not_called()

    async def test_lock_released_on_exception(self):
        """Lock must be released even if an unexpected exception occurs."""
        broadcaster = AsyncMock()
        with patch("db.get_db", side_effect=RuntimeError("DB error")):
            await compact.maybe_compact_db_history(1, 42, "deepseek", "deepseek-chat", 0.7, broadcaster)
        self.assertNotIn(1, compact._db_compaction_locks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
