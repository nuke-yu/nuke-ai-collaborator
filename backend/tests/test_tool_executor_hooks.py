"""Tests for tool_executor: condition filtering, once hooks, asyncRewake queue."""
import asyncio
import pytest
from executors import tool_executor as te


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _restore_global_state():
    """Clear module-global hooks AND the tool registry after every test.

    These tests register a `run_shell` ToolDef into the global
    tool_executor._defs (setup_method). Without this teardown it leaks into
    later test files: test_abort_signal builds tool_schemas from get_schemas(),
    and a leaked `run_shell` flips tool_loop_v1 off its pure-streaming branch,
    breaking that test's isolation.
    """
    yield
    te.clear_before_hooks()
    te.clear_after_hooks()
    te._registry.clear()


def _make_async(return_value=None, block_with_reason: str | None = None):
    """Return an async hook function; optionally blocks with a reason."""
    async def hook(name, arguments, *args):
        if block_with_reason:
            return {"block": True, "reason": block_with_reason}
        return return_value
    return hook


# ---------------------------------------------------------------------------
# _condition_matches
# ---------------------------------------------------------------------------

class TestConditionMatches:
    def test_exact_name_match(self):
        assert te._condition_matches("run_shell", "run_shell", {})

    def test_exact_name_no_match(self):
        assert not te._condition_matches("run_shell", "read_file", {})

    def test_glob_name_match(self):
        assert te._condition_matches("*_file", "read_file", {})
        assert te._condition_matches("*_file", "write_file", {})

    def test_glob_name_no_match(self):
        assert not te._condition_matches("*_file", "run_shell", {})

    def test_args_pattern_any_value_match(self):
        assert te._condition_matches("run_shell(git *)", "run_shell", {"cmd": "git status"})

    def test_args_pattern_no_match(self):
        assert not te._condition_matches("run_shell(git *)", "run_shell", {"cmd": "ls -la"})

    def test_args_pattern_skips_none(self):
        assert not te._condition_matches("run_shell(git *)", "run_shell", {"cmd": None})

    def test_no_args_pattern_always_matches(self):
        assert te._condition_matches("run_shell", "run_shell", {"cmd": "anything"})

    def test_malformed_condition_returns_true(self):
        # Malformed condition fails open (hook runs)
        assert te._condition_matches("", "run_shell", {})


# ---------------------------------------------------------------------------
# Before-hook condition filtering
# ---------------------------------------------------------------------------

async def _shell_handler(cmd=None, **_):
    return "ok"


async def _shell_handler_original(cmd=None, **_):
    return "original"


async def _shell_handler_done(cmd=None, **_):
    return "done"


class TestArgAliasNormalization:
    """LLMs often call write_file with `file_path`/`contents` instead of the
    declared `path`/`content`. execute() should normalize the alias rather than
    crash with 'unexpected keyword argument'."""

    def _register_write_file(self, captured):
        from executors.base import ToolDef
        async def handler(path, content):
            captured["path"] = path
            captured["content"] = content
            return "ok"
        te.register(ToolDef(name="write_file", description="", parameters={}), handler)

    def test_file_path_alias_maps_to_path(self):
        captured = {}
        self._register_write_file(captured)
        result, is_error = _run(te.execute("write_file", {"file_path": "a.txt", "content": "x"}))
        assert not is_error
        assert result == "ok"
        assert captured == {"path": "a.txt", "content": "x"}

    def test_contents_alias_maps_to_content(self):
        captured = {}
        self._register_write_file(captured)
        _run(te.execute("write_file", {"path": "a.txt", "contents": "body"}))
        assert captured == {"path": "a.txt", "content": "body"}

    def test_explicit_path_wins_over_alias(self):
        captured = {}
        self._register_write_file(captured)
        # both present → keep the canonical, drop the alias (no crash)
        _run(te.execute("write_file", {"path": "real.txt", "file_path": "alias.txt", "content": "x"}))
        assert captured["path"] == "real.txt"

    def test_no_alias_left_untouched(self):
        captured = {}
        self._register_write_file(captured)
        _run(te.execute("write_file", {"path": "a.txt", "content": "x"}))
        assert captured == {"path": "a.txt", "content": "x"}


class TestBeforeHookCondition:
    def setup_method(self):
        te.clear_before_hooks()
        te.clear_after_hooks()
        # Register a no-op tool so execution can reach hooks
        from executors.base import ToolDef
        td = ToolDef(name="run_shell", description="", parameters={})
        te.register(td, _shell_handler)

    def test_hook_skipped_when_condition_no_match(self):
        fired = []

        async def hook(name, arguments, ctx):
            fired.append(name)

        te.add_before_hook(hook, condition="write_file")
        _run(te.execute("run_shell", {"cmd": "ls"}, {}))
        assert not fired, "hook should not fire for non-matching tool"

    def test_hook_fires_when_condition_matches(self):
        fired = []

        async def hook(name, arguments, ctx):
            fired.append(name)

        te.add_before_hook(hook, condition="run_shell")
        _run(te.execute("run_shell", {"cmd": "ls"}, {}))
        assert fired == ["run_shell"]

    def test_hook_blocks_when_condition_matches(self):
        te.add_before_hook(_make_async(block_with_reason="blocked"), condition="run_shell")
        result, is_error = _run(te.execute("run_shell", {"cmd": "ls"}, {}))
        assert "[已拦截]" in result
        assert is_error is True
        assert "blocked" in result

    def test_args_condition_fires_only_for_matching_args(self):
        fired_cmds = []

        async def hook(name, arguments, ctx):
            fired_cmds.append(arguments.get("cmd"))

        te.add_before_hook(hook, condition="run_shell(git *)")
        _run(te.execute("run_shell", {"cmd": "git status"}, {}))
        _run(te.execute("run_shell", {"cmd": "ls"}, {}))
        assert fired_cmds == ["git status"]


# ---------------------------------------------------------------------------
# Once hooks
# ---------------------------------------------------------------------------

class TestOnceHooks:
    def setup_method(self):
        te.clear_before_hooks()
        te.clear_after_hooks()
        from executors.base import ToolDef
        td = ToolDef(name="run_shell", description="", parameters={})
        te.register(td, _shell_handler)

    def test_before_hook_once_removed_after_firing(self):
        fired_count = [0]

        async def hook(name, arguments, ctx):
            fired_count[0] += 1

        te.add_before_hook(hook, once=True)
        _run(te.execute("run_shell", {}, {}))
        _run(te.execute("run_shell", {}, {}))
        assert fired_count[0] == 1
        assert not any(e.fn is hook for e in te._before_hooks)

    def test_after_hook_once_removed_after_firing(self):
        fired_count = [0]

        async def hook(name, arguments, result, ctx):
            fired_count[0] += 1

        te.add_after_hook(hook, once=True)
        _run(te.execute("run_shell", {}, {}))
        _run(te.execute("run_shell", {}, {}))
        assert fired_count[0] == 1
        assert not any(e.fn is hook for e in te._after_hooks)

    def test_once_blocking_hook_removed_after_block(self):
        te.add_before_hook(_make_async(block_with_reason="one-time-block"), once=True)
        first, _ = _run(te.execute("run_shell", {}, {}))
        second, _ = _run(te.execute("run_shell", {}, {}))
        assert "[已拦截]" in first
        assert "[已拦截]" not in second  # hook gone after first fire

    def test_idempotent_registration(self):
        async def hook(name, arguments, ctx):
            pass

        te.add_before_hook(hook)
        te.add_before_hook(hook)
        assert sum(1 for e in te._before_hooks if e.fn is hook) == 1

    def test_concurrent_execute_with_once_hook_no_valueerror(self):
        """DFT-026: two execute() calls racing the same `once` hook must not
        crash with ValueError(list.remove) nor double-fire."""
        fired = []

        async def gate(name, arguments, ctx):
            # yield control so both execute() coroutines interleave inside the
            # before-hook loop before either removes the shared `once` entry
            await asyncio.sleep(0)
            fired.append(name)

        te.add_before_hook(gate, once=True)

        async def race():
            return await asyncio.gather(
                te.execute("run_shell", {}, {}),
                te.execute("run_shell", {}, {}),
            )

        results = _run(race())  # must not raise
        assert all("[钩子错误]" not in r for r in results)
        assert len(fired) == 1  # once hook fired exactly once
        assert not any(e.fn is gate for e in te._before_hooks)


# ---------------------------------------------------------------------------
# After-hook result transformation
# ---------------------------------------------------------------------------

class TestAfterHookTransform:
    def setup_method(self):
        te.clear_before_hooks()
        te.clear_after_hooks()
        from executors.base import ToolDef
        td = ToolDef(name="run_shell", description="", parameters={})
        te.register(td, _shell_handler_original)

    def test_after_hook_can_replace_result(self):
        async def hook(name, arguments, result, ctx):
            return "replaced"

        te.add_after_hook(hook)
        result, _ = _run(te.execute("run_shell", {}, {}))
        assert result == "replaced"

    def test_after_hook_returning_none_preserves_result(self):
        async def hook(name, arguments, result, ctx):
            return None  # no-op

        te.add_after_hook(hook)
        result, _ = _run(te.execute("run_shell", {}, {}))
        assert result == "original"

    def test_after_hooks_chain(self):
        async def hook1(name, arguments, result, ctx):
            return result + "+h1"

        async def hook2(name, arguments, result, ctx):
            return result + "+h2"

        te.add_after_hook(hook1)
        te.add_after_hook(hook2)
        result, _ = _run(te.execute("run_shell", {}, {}))
        assert result == "original+h1+h2"

# ---------------------------------------------------------------------------
# asyncRewake queue injection
# ---------------------------------------------------------------------------

class TestAsyncRewake:
    def setup_method(self):
        te.clear_before_hooks()
        te.clear_after_hooks()
        from executors.base import ToolDef
        td = ToolDef(name="run_shell", description="", parameters={})
        te.register(td, _shell_handler_done)

    def test_rewake_message_put_into_queue(self):
        rewake_queue: asyncio.Queue = asyncio.Queue()

        async def rewake_hook(name, arguments, result, ctx):
            await ctx["rewake_queue"].put("[系统唤醒] post-tool check")

        te.add_after_hook(rewake_hook)
        _run(te.execute("run_shell", {}, {"rewake_queue": rewake_queue}))
        assert not rewake_queue.empty()
        msg = rewake_queue.get_nowait()
        assert "系统唤醒" in msg

    def test_rewake_hook_not_put_when_queue_absent(self):
        """Hook that puts to queue should not error when queue not in context."""
        async def rewake_hook(name, arguments, result, ctx):
            q = ctx.get("rewake_queue")
            if q:
                await q.put("wake")

        te.add_after_hook(rewake_hook)
        result, _ = _run(te.execute("run_shell", {}, {}))
        assert result == "done"

