# P1 稳定性/安全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现三个 P1 稳定性/安全功能：API 限流重试、敏感路径保护、死循环防护。

**Architecture:** 三个功能相互独立。限流重试在 `ai_client.py` 中加 `AIRateLimitError` + 指数退避重试逻辑；敏感路径保护在 `workspace_tools.py` 中加路径检查钩子；死循环防护在 `tool_loop_v1.py` 中加连续纯工具轮次计数器。

**Tech Stack:** Python asyncio, httpx, unittest.mock

---

## 文件结构

| 文件 | 变更类型 | 职责 |
|------|---------|------|
| `backend/ai_client.py` | 修改 | 新增 `AIRateLimitError`、`_parse_retry_after()`、429 检测、`call_ai_once` 重试循环 + `fallback_model` 参数 |
| `backend/executors/plugins/workspace_tools.py` | 修改 | 新增 `_SENSITIVE_PATH_PREFIXES`、`_SENSITIVE_FILENAME_PATTERNS`、`_is_sensitive_path()`，在 read/write local file handler 中加检查 |
| `backend/executors/plugins/tool_loop_v1.py` | 修改 | 新增 `_DOOM_LOOP_THRESHOLD = 5` 常量，while 循环内加 `_consecutive_tool_only` 计数器 |
| `backend/tests/test_ai_client.py` | 修改 | 新增限流重试测试 |
| `backend/tests/test_p1_safety.py` | 新建 | 敏感路径 + 死循环防护测试 |

---

## Task 1: Retry + Rate Limit Handling

**Files:**
- Modify: `backend/ai_client.py`
- Test: `backend/tests/test_ai_client.py`

- [ ] **Step 1: 写失败测试（`_parse_retry_after` 和 `AIRateLimitError`）**

在 `test_ai_client.py` 末尾、`if __name__` 之前插入：

```python
class TestRetryAndRateLimit(unittest.IsolatedAsyncioTestCase):

    def test_parse_retry_after_ms(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after-ms": "5000"}), 5.0)

    def test_parse_retry_after_seconds(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after": "3"}), 3.0)

    def test_parse_retry_after_ms_takes_priority(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after-ms": "2000", "retry-after": "10"}), 2.0)

    def test_parse_retry_after_no_header_returns_default(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({}), 2.0)

    def test_parse_retry_after_invalid_value_returns_default(self):
        from ai_client import _parse_retry_after
        self.assertAlmostEqual(_parse_retry_after({"retry-after": "not-a-number"}), 2.0)

    async def test_call_ai_once_retries_on_rate_limit_and_succeeds(self):
        from ai_client import call_ai_once
        call_count = 0

        async def fake_dispatch(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                from ai_client import AIRateLimitError
                raise AIRateLimitError(0.001)  # tiny wait for test speed
            return {"type": "text", "content": "ok"}

        with patch("ai_client._dispatch_once", new=fake_dispatch), \
             patch("ai_client._keys", return_value={}), \
             patch("ai_client.asyncio.sleep", new=AsyncMock()):
            result = await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                        provider="deepseek", model="deepseek-chat")

        self.assertEqual(result["content"], "ok")
        self.assertEqual(call_count, 2)

    async def test_call_ai_once_exhausted_raises_ai_error(self):
        from ai_client import call_ai_once, AIError, AIRateLimitError

        async def always_rate_limit(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            raise AIRateLimitError(0.001)

        with patch("ai_client._dispatch_once", new=always_rate_limit), \
             patch("ai_client._keys", return_value={}), \
             patch("ai_client.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(AIError):
                await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                   provider="deepseek", model="deepseek-chat")

    async def test_call_ai_once_uses_fallback_model_after_rate_limit(self):
        from ai_client import call_ai_once, AIRateLimitError
        tried_models = []

        async def fake_dispatch(provider, model, keys, sp, msgs, temp, max_tok, tools, use_cached_mc):
            tried_models.append(model)
            if model == "main-model":
                raise AIRateLimitError(0.001)
            return {"type": "text", "content": "fallback ok"}

        with patch("ai_client._dispatch_once", new=fake_dispatch), \
             patch("ai_client._keys", return_value={}), \
             patch("ai_client.asyncio.sleep", new=AsyncMock()):
            result = await call_ai_once("sp", [{"role": "user", "content": "hi"}],
                                        provider="deepseek", model="main-model",
                                        fallback_model="fallback-model")

        self.assertIn("fallback-model", tried_models)
        self.assertEqual(result["content"], "fallback ok")
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
/opt/homebrew/bin/python3.13 -m pytest tests/test_ai_client.py::TestRetryAndRateLimit -v
```

Expected: FAIL — `ImportError: cannot import name '_parse_retry_after'` or similar.

- [ ] **Step 3: 实现 `AIRateLimitError`、`_parse_retry_after`、`_dispatch_once`，修改 `call_ai_once`**

在 `backend/ai_client.py` 中做以下修改：

**3a. 在文件顶部加 `import asyncio`（在现有 import 之后）：**

```python
import asyncio
```

**3b. 在 `AIContextOverflowError` 类之后，加新的错误类和辅助函数：**

```python
class AIRateLimitError(AIError):
    """Raised on HTTP 429 rate limit. Carries recommended wait time in seconds."""
    def __init__(self, wait_seconds: float = 2.0):
        self.wait_seconds = wait_seconds
        super().__init__(f"API 限流，建议等待 {wait_seconds:.1f}s 后重试")


_AI_RETRY_MAX = 3


def _parse_retry_after(headers: dict) -> float:
    """Parse retry-after-ms or retry-after from response headers. Returns seconds."""
    if val := headers.get("retry-after-ms"):
        try:
            return float(val) / 1000
        except (ValueError, TypeError):
            pass
    if val := headers.get("retry-after"):
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return 2.0
```

**3c. 在 `_once_openai_compat` 中，在 400/413 检查之前插入 429 检测：**

找到这段代码（约第 218 行）：
```python
        if resp.status_code in (400, 413):
```

改为：
```python
        if resp.status_code == 429:
            wait = _parse_retry_after(dict(resp.headers))
            raise AIRateLimitError(wait)
        if resp.status_code in (400, 413):
```

**3d. 在 `_once_claude` 中同样插入 429 检测：**

找到这段代码（约第 267 行）：
```python
        if resp.status_code in (400, 413):
```

改为：
```python
        if resp.status_code == 429:
            wait = _parse_retry_after(dict(resp.headers))
            raise AIRateLimitError(wait)
        if resp.status_code in (400, 413):
```

**3e. 在 `call_ai_once` 之前，插入 `_dispatch_once` 辅助函数：**

```python
async def _dispatch_once(
    provider: str, model: str, keys: dict,
    system_prompt: str, messages: list,
    temperature: float, max_tokens: int,
    tools: list | None, use_cached_microcompact: bool,
) -> dict:
    """Single provider dispatch — no retry logic. Called by call_ai_once."""
    if provider in ("deepseek", "openai"):
        url = ("https://api.deepseek.com/v1/chat/completions" if provider == "deepseek"
               else "https://api.openai.com/v1/chat/completions")
        label = "DeepSeek" if provider == "deepseek" else "OpenAI"
        api_key = _require_key(keys, "deepseek" if provider == "deepseek" else "openai", label)
        return await _once_openai_compat(url, api_key, model, system_prompt,
                                         messages, temperature, max_tokens, tools)
    elif provider == "claude":
        api_key = _require_key(keys, "anthropic", "Anthropic")
        return await _once_claude(api_key, model, system_prompt,
                                  messages, temperature, max_tokens, tools,
                                  use_cached_microcompact=use_cached_microcompact)
    elif provider == "ollama":
        url = f"{keys['ollama_url']}/v1/chat/completions"
        return await _once_openai_compat(url, "", model, system_prompt,
                                         messages, temperature, max_tokens, None)
    else:
        raise AIError(f"不支持的模型提供商: {provider}")
```

**3f. 将 `call_ai_once` 的签名和函数体改写如下（完整替换）：**

```python
async def call_ai_once(
    system_prompt: str,
    messages: list[dict],
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
    use_cached_microcompact: bool = False,
    fallback_model: str = "",
) -> dict:
    """Single non-streaming AI call with retry on rate limit.

    Retries up to _AI_RETRY_MAX times with exponential backoff on HTTP 429.
    If fallback_model is set, tries it once after main model is exhausted.
    """
    keys = _keys()
    models_to_try = [model]
    if fallback_model and fallback_model != model:
        models_to_try.append(fallback_model)

    last_error: Exception | None = None
    for try_model in models_to_try:
        for attempt in range(_AI_RETRY_MAX):
            try:
                return await _dispatch_once(
                    provider, try_model, keys, system_prompt, messages,
                    temperature, max_tokens, tools, use_cached_microcompact,
                )
            except AIRateLimitError as e:
                last_error = e
                if attempt < _AI_RETRY_MAX - 1:
                    wait = max(e.wait_seconds, 2.0 ** attempt)
                    await asyncio.sleep(wait)
                # On last attempt: fall through, try next model (if any)
            except AIContextOverflowError:
                raise
            except AIError:
                raise
            except httpx.TimeoutException:
                raise AIError("AI 响应超时，请稍后重试")
            except httpx.HTTPStatusError as e:
                raise AIError(f"AI 服务异常（{e.response.status_code}）")
            except Exception as e:
                raise AIError(f"AI 调用失败：{str(e)}")

    raise AIError(f"API 限流，已重试 {_AI_RETRY_MAX} 次仍失败") from last_error
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
/opt/homebrew/bin/python3.13 -m pytest tests/test_ai_client.py -v
```

Expected: ALL PASS（原有测试 + 新增测试）

- [ ] **Step 5: Commit**

```bash
git add backend/ai_client.py backend/tests/test_ai_client.py
git commit -m "feat: API限流重试 + retry-after解析 + fallback_model支持"
```

---

## Task 2: 敏感路径兜底保护

**Files:**
- Modify: `backend/executors/plugins/workspace_tools.py`
- Create: `backend/tests/test_p1_safety.py`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_p1_safety.py`：

```python
"""Tests for P1 safety features: sensitive path protection and doom loop guard."""
import sys
import os
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Sensitive path protection
# ---------------------------------------------------------------------------

class TestSensitivePathDetection(unittest.TestCase):

    def _is_sensitive(self, path: str) -> bool:
        from executors.plugins.workspace_tools import _is_sensitive_path
        return _is_sensitive_path(path)

    def test_ssh_dir_blocked(self):
        self.assertTrue(self._is_sensitive("~/.ssh/id_rsa"))

    def test_ssh_known_hosts_blocked(self):
        self.assertTrue(self._is_sensitive("~/.ssh/known_hosts"))

    def test_aws_credentials_blocked(self):
        self.assertTrue(self._is_sensitive("~/.aws/credentials"))

    def test_aws_config_blocked(self):
        self.assertTrue(self._is_sensitive("~/.aws/config"))

    def test_dotenv_file_blocked(self):
        self.assertTrue(self._is_sensitive("/project/.env"))

    def test_dotenv_local_blocked(self):
        self.assertTrue(self._is_sensitive("/project/.env.local"))

    def test_pem_key_blocked(self):
        self.assertTrue(self._is_sensitive("/certs/server.pem"))

    def test_private_key_blocked(self):
        self.assertTrue(self._is_sensitive("/certs/server.key"))

    def test_normal_file_allowed(self):
        self.assertFalse(self._is_sensitive("/home/user/project/main.py"))

    def test_normal_env_dir_allowed(self):
        # a directory named "env" (not .env file) should be allowed
        self.assertFalse(self._is_sensitive("/home/user/project/env/activate"))

    def test_dotenv_example_allowed(self):
        # .env.example is commonly committed and not sensitive
        self.assertFalse(self._is_sensitive("/project/.env.example"))


class TestSensitivePathHandlers(unittest.IsolatedAsyncioTestCase):

    async def test_read_local_file_blocked_for_ssh(self):
        from executors.plugins.workspace_tools import _handle_read_local_file
        result = await _handle_read_local_file("~/.ssh/id_rsa")
        self.assertIn("安全拒绝", result)
        self.assertIn("~/.ssh/id_rsa", result)

    async def test_write_local_file_blocked_for_dotenv(self):
        from executors.plugins.workspace_tools import _handle_write_local_file
        result = await _handle_write_local_file("/project/.env", "SECRET=abc")
        self.assertIn("安全拒绝", result)

    async def test_read_local_file_allowed_for_normal_path(self):
        from executors.plugins.workspace_tools import _handle_read_local_file
        # should reach the real read attempt and fail with FileNotFoundError, not a security block
        result = await _handle_read_local_file("/nonexistent/normal/file.txt")
        self.assertNotIn("安全拒绝", result)
        self.assertIn("文件不存在", result)
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
/opt/homebrew/bin/python3.13 -m pytest tests/test_p1_safety.py::TestSensitivePathDetection tests/test_p1_safety.py::TestSensitivePathHandlers -v
```

Expected: FAIL — `ImportError: cannot import name '_is_sensitive_path'`

- [ ] **Step 3: 实现 `_is_sensitive_path` 和 handler 中的检查**

在 `backend/executors/plugins/workspace_tools.py` 中：

**3a. 在文件顶部的 import 之后（`from pathlib import Path` 之后）加：**

```python
import fnmatch
```

**3b. 在 `_DANGEROUS_PATTERNS` 定义之前，加以下常量和函数：**

```python
# Sensitive path prefixes — expanded at runtime with Path.expanduser()
_SENSITIVE_PATH_PREFIXES = [
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config/gcloud",
    "~/.kube",
]

# Sensitive filename patterns (fnmatch style)
_SENSITIVE_FILENAME_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "credentials",
    ".netrc",
    "*.pfx",
    "*.p12",
]

# Filenames explicitly allowed despite matching a broad pattern above
_SENSITIVE_FILENAME_ALLOWLIST = {
    ".env.example",
    ".env.sample",
    ".env.template",
}


def _is_sensitive_path(path: str) -> bool:
    """Return True if path points to a sensitive location that must not be read or written."""
    p = Path(path).expanduser()
    p_str = str(p)
    filename = p.name

    # Allowlist check first
    if filename in _SENSITIVE_FILENAME_ALLOWLIST:
        return False

    # Directory prefix check
    for prefix in _SENSITIVE_PATH_PREFIXES:
        expanded = str(Path(prefix).expanduser())
        if p_str.startswith(expanded):
            return True

    # Filename pattern check
    for pattern in _SENSITIVE_FILENAME_PATTERNS:
        if fnmatch.fnmatch(filename, pattern):
            return True

    return False
```

**3c. 修改 `_handle_read_local_file`，在 try 之前加检查：**

将现有函数：
```python
async def _handle_read_local_file(path: str, context: dict = None) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[文件不存在] {path}"
    except Exception as e:
        return f"[读取错误] {e}"
```

改为：
```python
async def _handle_read_local_file(path: str, context: dict = None) -> str:
    if _is_sensitive_path(path):
        return f"[安全拒绝] 不允许读取敏感路径：{path}"
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[文件不存在] {path}"
    except Exception as e:
        return f"[读取错误] {e}"
```

**3d. 修改 `_handle_write_local_file`，在 try 之前加检查：**

将现有函数：
```python
async def _handle_write_local_file(path: str, content: str, context: dict = None) -> str:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"
    except Exception as e:
        return f"[写入错误] {e}"
```

改为：
```python
async def _handle_write_local_file(path: str, content: str, context: dict = None) -> str:
    if _is_sensitive_path(path):
        return f"[安全拒绝] 不允许写入敏感路径：{path}"
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"
    except Exception as e:
        return f"[写入错误] {e}"
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
/opt/homebrew/bin/python3.13 -m pytest tests/test_p1_safety.py::TestSensitivePathDetection tests/test_p1_safety.py::TestSensitivePathHandlers -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/executors/plugins/workspace_tools.py backend/tests/test_p1_safety.py
git commit -m "feat: 敏感路径兜底保护（read/write_local_file bypass-immune）"
```

---

## Task 3: 死循环保护

**Files:**
- Modify: `backend/executors/plugins/tool_loop_v1.py`
- Modify: `backend/tests/test_p1_safety.py`

- [ ] **Step 1: 写失败测试**

在 `test_p1_safety.py` 末尾、`if __name__` 之前（若无则在文件末尾）追加：

```python
# ---------------------------------------------------------------------------
# Doom loop protection
# ---------------------------------------------------------------------------

class TestDoomLoopProtection(unittest.IsolatedAsyncioTestCase):
    """Verify the tool loop breaks after _DOOM_LOOP_THRESHOLD consecutive tool-only rounds."""

    def _make_execution_context(self):
        """Build a minimal ExecutionContext-like object for ToolLoopV1.run()."""
        from executors.base import ExecutionContext
        broadcaster = AsyncMock()
        broadcaster.broadcast = AsyncMock()
        return ExecutionContext(
            bot={
                "id": 1, "name": "TestBot", "system_prompt": "test",
                "model_name": "deepseek-chat", "model_provider": "deepseek",
                "temperature": 0.7, "max_tokens": 512,
                "avatar_color": "#000", "role": "assistant",
                "executor_config": None, "personality_prompt": None,
            },
            group_id=1,
            user_message="do something",
            sender={"id": 0, "name": "user", "type": "user", "avatar_color": "#fff"},
            history=[],
            all_bots=[],
            all_members=[],
            broadcaster=broadcaster,
            spawn_depth=1,  # spawn_depth>0 → skip DB save, return immediately
        )

    async def test_doom_loop_breaks_after_threshold(self):
        """When AI returns tool_calls every round, loop should break at DOOM_LOOP_THRESHOLD."""
        from executors.plugins.tool_loop_v1 import ToolLoopV1, _DOOM_LOOP_THRESHOLD

        executor = ToolLoopV1()
        ctx = self._make_execution_context()

        call_count = 0

        async def fake_call_ai_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "type": "tool_calls",
                "calls": [{"id": "c1", "name": "list_workspace", "arguments": {}}],
                "assistant_message": {"role": "assistant", "content": None, "tool_calls": []},
            }

        async def fake_tool_execute(name, arguments, context=None):
            return "[]"

        with patch("executors.plugins.tool_loop_v1.call_ai_once", new=fake_call_ai_once), \
             patch("executors.plugins.tool_loop_v1.tool_executor") as mock_te, \
             patch("executors.plugins.tool_loop_v1.list_skills", return_value=[]), \
             patch("executors.plugins.tool_loop_v1.load_always_skills", return_value=[]), \
             patch("executors.plugins.tool_loop_v1.filter_skills_by_context", side_effect=lambda s, _: s), \
             patch("executors.plugins.tool_loop_v1.get_memory_context", new=AsyncMock(return_value="")), \
             patch("executors.plugins.tool_loop_v1.load_context_files", new=AsyncMock(return_value=[])), \
             patch("executors.plugins.tool_loop_v1.format_context_blocks", return_value=""), \
             patch("executors.plugins.tool_loop_v1.build_context_message",
                   return_value=([], "do something")), \
             patch("executors.plugins.tool_loop_v1.compact.apply_tool_result_microcompact",
                   side_effect=lambda m: m), \
             patch("executors.plugins.tool_loop_v1.compact.snip_if_needed",
                   side_effect=lambda m, n: (m, False)), \
             patch("executors.plugins.tool_loop_v1.compact.auto_compact_if_needed",
                   new=AsyncMock(return_value=([], False))), \
             patch("executors.plugins.tool_loop_v1.compact.estimate_tokens", return_value=0), \
             patch("executors.plugins.tool_loop_v1.compact.should_use_cached_microcompact", return_value=False), \
             patch("executors.plugins.tool_loop_v1.register_workspace_tools"):
            mock_te.get_schemas = MagicMock(return_value=[{"name": "list_workspace"}])
            mock_te.execute = AsyncMock(return_value="[]")

            result = await executor.run(ctx)

        # Should break after _DOOM_LOOP_THRESHOLD rounds, not run to max_iter (10)
        self.assertLessEqual(call_count, _DOOM_LOOP_THRESHOLD + 1)
        self.assertIn("循环保护", result.full_text)

    async def test_doom_loop_threshold_constant_exists(self):
        from executors.plugins.tool_loop_v1 import _DOOM_LOOP_THRESHOLD
        self.assertIsInstance(_DOOM_LOOP_THRESHOLD, int)
        self.assertGreaterEqual(_DOOM_LOOP_THRESHOLD, 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
/opt/homebrew/bin/python3.13 -m pytest tests/test_p1_safety.py::TestDoomLoopProtection -v
```

Expected: FAIL — `ImportError: cannot import name '_DOOM_LOOP_THRESHOLD'`

- [ ] **Step 3: 在 `tool_loop_v1.py` 中实现死循环保护**

**3a. 在 `ToolLoopV1` 类定义之前，加常量：**

在文件中找到 `class ToolLoopV1(BotExecutor):` 这一行，在它前面（和 `_run_fork_skill` 函数之后）插入：

```python
_DOOM_LOOP_THRESHOLD = 5  # consecutive tool-only rounds before force-breaking
```

**3b. 在 `run()` 方法的 `while iter_count < max_iter:` 循环内，在 `iter_count += 1` 之前加初始化（在 `tool_schemas = ...` 之后，`while` 循环开始位置）：**

找到 `while iter_count < max_iter:` 这一行，在进入 while 循环之前（即紧接在 `tool_schemas = tool_executor.get_schemas(tool_names)` 之后）添加：

```python
        _consecutive_tool_only = 0
```

**3c. 在 while 循环内，找到处理 `tool_calls` 的分支结尾处（`# Post-tool-round: 3-strategy compaction pipeline` 注释所在代码块末尾，即 steer 注入之后），在 `else:` 分支之前加保护检查：**

找到这段：
```python
                        # Inject any pending steer messages before the next AI call
                        if ctx.steer_channel and not ctx.steer_channel.empty():
                            ...
                    else:
                        # Tools resolved — stream the final answer properly
                        await _finalize_reply()
                        break
```

在 `else:` 之前，将整个 `if result["type"] == "tool_calls":` 块的末尾（steer 注入之后）加：

完整的修改是：将 `if result["type"] == "tool_calls":` 块改为包含计数器逻辑：

```python
                    if result["type"] == "tool_calls":
                        _consecutive_tool_only += 1
                        if _consecutive_tool_only >= _DOOM_LOOP_THRESHOLD:
                            full_text = (
                                f"[循环保护] AI 已连续 {_consecutive_tool_only} 轮仅调用工具未输出文字，"
                                f"强制中断。最后调用的工具：{result['calls'][-1]['name']}"
                            )
                            break
                        messages.append(result["assistant_message"])
                        # ... （后续 for call in result["calls"] 等已有代码保持不变）
```

**注意**：只在 `if result["type"] == "tool_calls":` 块开头插入计数器逻辑，已有的所有工具处理代码不变。完整改动如下：

在 `if result["type"] == "tool_calls":` 这一行后面，紧接着插入：

```python
                        _consecutive_tool_only += 1
                        if _consecutive_tool_only >= _DOOM_LOOP_THRESHOLD:
                            full_text = (
                                f"[循环保护] AI 已连续 {_consecutive_tool_only} 轮仅调用工具未输出文字，"
                                f"强制中断。最后调用的工具：{result['calls'][-1]['name']}"
                            )
                            break
```

在 `else:` 分支（`# Tools resolved — stream the final answer properly`）开头，插入：

```python
                        _consecutive_tool_only = 0
```

所以完整结构变为：
```python
                    if result["type"] == "tool_calls":
                        _consecutive_tool_only += 1
                        if _consecutive_tool_only >= _DOOM_LOOP_THRESHOLD:
                            full_text = (
                                f"[循环保护] AI 已连续 {_consecutive_tool_only} 轮仅调用工具未输出文字，"
                                f"强制中断。最后调用的工具：{result['calls'][-1]['name']}"
                            )
                            break
                        messages.append(result["assistant_message"])
                        for call in result["calls"]:
                            # ... 已有工具调用代码完全不变 ...
                        # Post-tool-round compaction pipeline ...
                        # Steer injection ...
                    else:
                        _consecutive_tool_only = 0
                        # Tools resolved — stream the final answer properly
                        await _finalize_reply()
                        break
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
/opt/homebrew/bin/python3.13 -m pytest tests/test_p1_safety.py -v
```

Expected: ALL PASS

- [ ] **Step 5: 运行全量测试，确认无回归**

```bash
/opt/homebrew/bin/python3.13 -m pytest tests/ -v 2>&1 | tail -20
```

Expected: 全部通过（数量 ≥ 156 + 新增测试）

- [ ] **Step 6: Commit**

```bash
git add backend/executors/plugins/tool_loop_v1.py backend/tests/test_p1_safety.py
git commit -m "feat: 死循环保护（连续 5 轮纯工具调用强制中断）"
```

---

## 自检

**Spec coverage:**
- ✅ Feature 1 (Retry + retry-after): `AIRateLimitError` + `_parse_retry_after` + `_dispatch_once` + `call_ai_once` retry loop + `fallback_model`
- ✅ Feature 2 (敏感路径): `_is_sensitive_path` + `_handle_read_local_file` + `_handle_write_local_file` 检查
- ✅ Feature 3 (死循环): `_DOOM_LOOP_THRESHOLD` + `_consecutive_tool_only` 计数器

**Placeholder scan:** 无 TBD / TODO / 模糊描述，所有步骤含完整代码。

**Type consistency:**
- `_dispatch_once` 签名与 `call_ai_once` 中调用方式一致
- `_is_sensitive_path(path: str) -> bool` 与 handler 调用一致
- `_DOOM_LOOP_THRESHOLD` 为 `int`，与 `_consecutive_tool_only >= _DOOM_LOOP_THRESHOLD` 比较一致
