from core import config
import asyncio
import json
import logging
import re
import uuid
import httpx
from contextlib import asynccontextmanager
from config import get_key

logger = logging.getLogger(__name__)

# DFT-033: one process-wide, connection-pooled AsyncClient instead of a fresh
# httpx.AsyncClient() per call. Per-call clients did a new TLS handshake every
# request/retry and only released the socket at GC; a shared pooled client reuses
# keep-alive connections and is closed deterministically on app shutdown.
_client: "httpx.AsyncClient | None" = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _client


@asynccontextmanager
async def _shared_client():
    """Borrow the shared client WITHOUT closing it on block exit.

    Drop-in for the old `async with httpx.AsyncClient(...) as client:` shape so
    call sites keep their structure; per-request timeouts are passed to each
    .post()/.stream() call. Lifecycle is owned by aclose_client().
    """
    yield _get_client()


async def aclose_client() -> None:
    """Close the shared client; call on app shutdown (main.py lifespan)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _keys():
    return {
        "deepseek": get_key("deepseek_api_key"),
        "openai": get_key("openai_api_key"),
        "anthropic": get_key("anthropic_api_key"),
        "ollama_url": get_key("ollama_base_url") or "http://localhost:11434",
    }

class AIError(Exception):
    pass

class AIContextOverflowError(AIError):
    """Raised when the API rejects the request due to context length exceeding model limit."""
    pass


class AIRateLimitError(AIError):
    """Raised on HTTP 429 rate limit. Carries recommended wait time in seconds."""
    def __init__(self, wait_seconds: float = 2.0):
        self.wait_seconds = wait_seconds
        super().__init__(f"API 限流，建议等待 {wait_seconds:.1f}s 后重试")


_AI_RETRY_MAX = config.AI_RETRY_MAX


def _parse_retry_after(headers: dict) -> float:
    """Parse retry-after-ms or retry-after from response headers. Returns seconds."""
    h = {k.lower(): v for k, v in headers.items()}
    if val := h.get("retry-after-ms"):
        try:
            return float(val) / 1000
        except (ValueError, TypeError):
            pass
    if val := h.get("retry-after"):
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return 2.0


_OVERFLOW_KEYWORDS = (
    "context_length_exceeded", "maximum context length",
    "too many tokens", "context window", "prompt is too long",
)

def _require_key(keys: dict, name: str, label: str) -> str:
    val = keys.get(name, "")
    if not val:
        raise AIError(f"未配置 {label} API Key，请在设置中填写")
    return val

# DFT-035: the old hardcoded DeepSeek get_embedding() lived here but had zero
# callers. Embeddings are now a config-driven chromadb backend in ai/embeddings.py
# (used by ai/memory.py); the DeepSeek/OpenAI API path moved there.

async def call_ai(system_prompt: str, history: list, user_message: str,
                  temperature: float = 0.7, max_tokens: int = 4096) -> str:
    """Non-streaming call (DeepSeek only, used for legacy/non-streaming paths)"""
    keys = _keys()
    api_key = _require_key(keys, "deepseek", "DeepSeek")
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})
    try:
        async with _shared_client() as client:
            response = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "deepseek-chat", "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                timeout=60,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        raise AIError("AI 响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise AIError(f"AI 服务异常（{e.response.status_code}）")
    except Exception as e:
        logger.error("AI call failed", exc_info=True)
        raise AIError("AI 调用失败，请稍后重试") from e

async def _stream_openai_compat(url: str, api_key: str, model: str, messages: list,
                                temperature: float = 0.7, max_tokens: int = 4096,
                                usage_out: list | None = None):
    body = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens, "stream": True,
    }
    if usage_out is not None:
        body["stream_options"] = {"include_usage": True}
    async with _shared_client() as client:
        async with client.stream(
            "POST", url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=120,
        ) as response:
            if response.status_code in (400, 413):
                body = await response.aread()
                try:
                    err_msg = str(json.loads(body).get("error", {}).get("message", ""))
                except Exception:
                    err_msg = body.decode(errors="replace")[:500]
                if any(k in err_msg.lower() for k in _OVERFLOW_KEYWORDS):
                    raise AIContextOverflowError(err_msg)
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                    if usage_out is not None and "usage" in data and data["usage"]:
                        raw = data["usage"]
                        usage_out.append({
                            "input_tokens": raw.get("prompt_tokens", 0),
                            "output_tokens": raw.get("completion_tokens", 0),
                            "cache_read_tokens": (
                                raw.get("prompt_cache_hit_tokens") or
                                (raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                            ),
                            "cache_creation_tokens": 0,
                        })
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

async def _stream_ollama(model: str, messages: list, base_url: str = "http://localhost:11434"):
    async with _shared_client() as client:
        async with client.stream(
            "POST", f"{base_url}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
            timeout=120,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

async def _stream_claude(model: str, system_prompt: str, messages: list, api_key: str = "",
                         temperature: float = 0.7, max_tokens: int = 4096,
                         usage_out: list | None = None):
    async with _shared_client() as client:
        async with client.stream(
            "POST", "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": messages,
                "stream": True,
            },
            timeout=120,
        ) as response:
            if response.status_code in (400, 413):
                body = await response.aread()
                try:
                    err_msg = str(json.loads(body).get("error", {}).get("message", ""))
                except Exception:
                    err_msg = body.decode(errors="replace")[:500]
                if any(k in err_msg.lower() for k in _OVERFLOW_KEYWORDS):
                    raise AIContextOverflowError(err_msg)
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                    event_type = data.get("type")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {}).get("text", "")
                        if delta:
                            yield delta
                    elif usage_out is not None:
                        if event_type == "message_start":
                            u = data.get("message", {}).get("usage", {})
                            if u:
                                usage_out.append({
                                    "input_tokens": u.get("input_tokens", 0),
                                    "output_tokens": 0,
                                    "cache_read_tokens": u.get("cache_read_input_tokens", 0),
                                    "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
                                })
                        elif event_type == "message_delta":
                            u = data.get("usage", {})
                            if u:
                                if usage_out:
                                    usage_out[-1]["output_tokens"] = u.get("output_tokens", 0)
                                else:
                                    usage_out.append({
                                        "input_tokens": 0, "output_tokens": u.get("output_tokens", 0),
                                        "cache_read_tokens": 0, "cache_creation_tokens": 0,
                                    })
                except (json.JSONDecodeError, KeyError):
                    continue

def _to_claude_messages(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-format messages (no system) to Claude format."""
    raw_result = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            tool_block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m.get("content") or ""
            }
            if raw_result and raw_result[-1]["role"] == "user" and isinstance(raw_result[-1]["content"], list):
                raw_result[-1]["content"].append(tool_block)
            else:
                raw_result.append({
                    "role": "user",
                    "content": [tool_block],
                })
        elif role == "assistant" and m.get("tool_calls"):
            content = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                args = tc["function"]["arguments"]
                content.append({
                    "type": "tool_use", "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(args) if isinstance(args, str) else args,
                })
            raw_result.append({"role": "assistant", "content": content})
        else:
            content = m.get("content", "")
            if isinstance(content, list):
                # Convert OpenAI image_url blocks to Claude image format
                converted = []
                for block in content:
                    if block.get("type") == "image_url":
                        url = (block.get("image_url") or {}).get("url", "")
                        converted.append({"type": "image", "source": {"type": "url", "url": url}})
                    else:
                        converted.append(block)
                raw_result.append({"role": role, "content": converted})
            else:
                raw_result.append({"role": role, "content": content})

    # 合并连续的同角色消息（例如连续的 user、user）以防 Anthropic API 报错
    merged_result = []
    for m in raw_result:
        if m["role"] == "system":
            continue
        if merged_result and merged_result[-1]["role"] == m["role"]:
            prev = merged_result[-1]
            def to_blocks(content):
                if isinstance(content, str):
                    return [{"type": "text", "text": content}] if content else []
                return content
            prev["content"] = to_blocks(prev["content"]) + to_blocks(m["content"])
        else:
            merged_result.append(m)

    return merged_result


# DSML（DeepSeek 内联函数调用标记）兜底解析。标记用全角竖线 ｜(U+FF5C) 包裹，形如
#   <｜｜DSML｜｜invoke name="write_file">
#     <｜｜DSML｜｜parameter name="path" string="true">index.html</｜｜DSML｜｜parameter>
#   </｜｜DSML｜｜invoke>
# 这里只认 invoke/parameter 标签名（忽略具体前缀/竖线），尽量宽容。
_DSML_INVOKE_RE = re.compile(r'invoke\s+name\s*=\s*(?:"|\')?([a-zA-Z0-9_-]+)(?:"|\')?\s*>(.*?)</[^>]*?invoke\s*>', re.DOTALL)
_DSML_PARAM_RE = re.compile(r'parameter\s+name\s*=\s*(?:"|\')?([a-zA-Z0-9_-]+)(?:"|\')?([^>]*)>(.*?)</[^>]*?parameter\s*>', re.DOTALL)


def _recover_dsml_tool_calls(content: str | None, usage: dict) -> dict | None:
    """把泄漏成文本的 DSML 工具调用救回成结构化 tool_calls 结果；非工具文本返回 None。

    产出的 assistant_message 是干净的 OpenAI 风格 tool_calls（content=None、arguments
    为 JSON 字符串），不含任何 DSML 文本——避免把乱码写回历史造成后续轮次模仿。"""
    if not content or "invoke" not in content:
        return None
    calls = []
    tc_msgs = []
    for name, body in _DSML_INVOKE_RE.findall(content):
        args: dict = {}
        for pname, attrs, raw in _DSML_PARAM_RE.findall(body):
            val = raw.strip()
            if 'string="true"' in attrs:
                args[pname] = val
            else:
                try:
                    args[pname] = json.loads(val)
                except Exception:
                    args[pname] = val
        cid = f"dsml_{uuid.uuid4().hex[:24]}"
        calls.append({"id": cid, "name": name, "arguments": args})
        tc_msgs.append({"id": cid, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})
    if not calls:
        return None
    logger.warning("recovered %d DSML-leaked tool call(s) from content: %s",
                   len(calls), [c["name"] for c in calls])
    assistant_message = {"role": "assistant", "content": None, "tool_calls": tc_msgs}
    return {"type": "tool_calls", "calls": calls,
            "assistant_message": assistant_message, "usage": usage}


def repair_json_arguments(s: str) -> dict | None:
    """
    Attempts to repair a truncated JSON arguments string.
    Usually looks like: {"path": "...", "content": "..."
    We scan the string, track open quotes, escapes, and brackets/braces,
    then append the necessary closing characters to make it valid JSON.
    """
    s = s.strip()
    if not s.startswith('{'):
        return None
        
    in_string = False
    escape = False
    stack = []
    
    # We will build a repaired string
    repaired = []
    
    for char in s:
        repaired.append(char)
        if escape:
            escape = False
            continue
        if char == '\\':
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in ('{', '['):
                stack.append(char)
            elif char in ('}', ']'):
                if stack:
                    stack.pop()
                    
    # Now close whatever is open
    if escape:
        repaired.pop() # remove the trailing backslash
    
    if in_string:
        repaired.append('"')
        
    # Close the open braces/brackets in reverse order
    while stack:
        open_char = stack.pop()
        if open_char == '{':
            repaired.append('}')
        elif open_char == '[':
            repaired.append(']')
            
    repaired_str = "".join(repaired)
    try:
        return json.loads(repaired_str)
    except Exception:
        return None


async def _once_openai_compat(url: str, api_key: str, model: str, system_prompt: str,
                               messages: list, temperature: float, max_tokens: int,
                               tools: list | None) -> dict:
    full_msgs = [{"role": "system", "content": system_prompt}] + messages
    body = {"model": model, "messages": full_msgs, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with _shared_client() as client:
        resp = await client.post(url, headers=headers, json=body, timeout=60)
        if resp.status_code == 429:
            wait = _parse_retry_after(dict(resp.headers))
            raise AIRateLimitError(wait)
        if resp.status_code in (400, 413):
            try:
                err_msg = str(resp.json().get("error", {}).get("message", ""))
            except Exception:
                err_msg = resp.text[:500]
            if any(k in err_msg.lower() for k in _OVERFLOW_KEYWORDS):
                raise AIContextOverflowError(err_msg)
        resp.raise_for_status()
        data = resp.json()
    choice = data["choices"][0]
    msg = choice["message"]
    raw_usage = data.get("usage", {})
    usage = {
        "input_tokens": raw_usage.get("prompt_tokens", 0),
        "output_tokens": raw_usage.get("completion_tokens", 0),
        "cache_read_tokens": (
            raw_usage.get("prompt_cache_hit_tokens") or
            (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        ),
        "cache_creation_tokens": 0,
    }
    if choice.get("finish_reason") in ("tool_calls", "length") and msg.get("tool_calls"):
        calls = []
        cleaned_tool_calls = []
        for tc in msg["tool_calls"]:
            func_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                repaired = repair_json_arguments(raw_args)
                if repaired is not None:
                    args = repaired
                    args["__truncated__"] = True
                else:
                    raise
            
            # Reconstruct the tool call with valid JSON for API history compatibility
            api_args = {k: v for k, v in args.items() if k != "__truncated__"}
            cleaned_tc = {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": json.dumps(api_args, ensure_ascii=False)
                }
            }
            cleaned_tool_calls.append(cleaned_tc)
            calls.append({"id": tc["id"], "name": func_name, "arguments": args})
            
        msg["tool_calls"] = cleaned_tool_calls
        return {"type": "tool_calls", "calls": calls, "assistant_message": msg, "usage": usage}
    content = msg.get("content", "") or ""
    # deepseek-chat 偶尔把工具调用以 DSML 文本塞进 content 而非结构化 tool_calls
    # 字段（finish_reason 也不是 tool_calls）。若不救回，调用就不会执行、DSML 乱码
    # 会被当成最终答复存库并污染后续上下文。
    recovered = _recover_dsml_tool_calls(content, usage)
    if recovered is not None:
        return recovered
    return {"type": "text", "content": content, "usage": usage}


async def _once_claude(api_key: str, model: str, system_prompt: str,
                        messages: list, temperature: float, max_tokens: int,
                        tools: list | None,
                        use_cached_microcompact: bool = False) -> dict:
    body = {
        "model": model, "max_tokens": max_tokens, "temperature": temperature,
        "system": system_prompt, "messages": _to_claude_messages(messages),
    }
    if tools:
        body["tools"] = [
            {"name": t["function"]["name"], "description": t["function"]["description"],
             "input_schema": t["function"]["parameters"]}
            for t in tools
        ]
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if use_cached_microcompact:
        body["context_management"] = {"type": "clear_tool_uses_20250919"}
        headers["anthropic-beta"] = "context-management-2025-09-19"
    async with _shared_client() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
            timeout=60,
        )
        if resp.status_code == 429:
            wait = _parse_retry_after(dict(resp.headers))
            raise AIRateLimitError(wait)
        if resp.status_code in (400, 413):
            try:
                err_body = resp.json()
                err_msg = str(err_body.get("error", {}).get("message", ""))
            except Exception:
                err_msg = resp.text[:500]
            if any(k in err_msg.lower() for k in _OVERFLOW_KEYWORDS):
                raise AIContextOverflowError(err_msg)
        resp.raise_for_status()
        data = resp.json()
    text_parts, tool_uses = [], []
    for block in data.get("content", []):
        if block["type"] == "text":
            text_parts.append(block["text"])
        elif block["type"] == "tool_use":
            tool_uses.append({"id": block["id"], "name": block["name"], "arguments": block["input"]})
    raw_usage = data.get("usage", {})
    usage = {
        "input_tokens": raw_usage.get("input_tokens", 0),
        "output_tokens": raw_usage.get("output_tokens", 0),
        "cache_read_tokens": raw_usage.get("cache_read_input_tokens", 0),
        "cache_creation_tokens": raw_usage.get("cache_creation_input_tokens", 0),
    }
    if tool_uses:
        assistant_msg = {
            "role": "assistant", "content": None,
            "tool_calls": [
                {"id": u["id"], "type": "function",
                 "function": {"name": u["name"], "arguments": json.dumps(u["arguments"])}}
                for u in tool_uses
            ],
        }
        return {"type": "tool_calls", "calls": tool_uses, "assistant_message": assistant_msg, "usage": usage}
    return {"type": "text", "content": "".join(text_parts), "usage": usage}


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
    If fallback_model is set, tries it once after main model retries are exhausted.
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
                # On last attempt: fall through to next model (if any)
            except AIContextOverflowError:
                raise
            except AIError:
                raise
            except httpx.TimeoutException:
                raise AIError("AI 响应超时，请稍后重试")
            except httpx.HTTPStatusError as e:
                raise AIError(f"AI 服务异常（{e.response.status_code}）")
            except Exception as e:
                logger.error("AI call failed", exc_info=True)
                raise AIError("AI 调用失败，请稍后重试") from e

    raise AIError(f"API 限流，已重试 {_AI_RETRY_MAX} 次仍失败") from last_error


async def call_ai_stream_messages(
    system_prompt: str,
    messages: list[dict],
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    usage_out: list | None = None,
):
    """Streaming call from pre-built OpenAI-format messages (no system). Used by tool_loop final response."""
    keys = _keys()
    try:
        if provider in ("deepseek", "openai"):
            url = ("https://api.deepseek.com/v1/chat/completions" if provider == "deepseek"
                   else "https://api.openai.com/v1/chat/completions")
            label = "DeepSeek" if provider == "deepseek" else "OpenAI"
            api_key = _require_key(keys, "deepseek" if provider == "deepseek" else "openai", label)
            full_msgs = [{"role": "system", "content": system_prompt}] + messages
            async for chunk in _stream_openai_compat(url, api_key, model, full_msgs, temperature, max_tokens, usage_out=usage_out):
                yield chunk
        elif provider == "claude":
            api_key = _require_key(keys, "anthropic", "Anthropic")
            claude_msgs = _to_claude_messages(messages)
            async for chunk in _stream_claude(model, system_prompt, claude_msgs, api_key, temperature, max_tokens, usage_out=usage_out):
                yield chunk
        elif provider == "ollama":
            full_msgs = [{"role": "system", "content": system_prompt}] + messages
            async for chunk in _stream_ollama(model, full_msgs, keys["ollama_url"]):
                yield chunk
        else:
            raise AIError(f"不支持的模型提供商: {provider}")
    except AIContextOverflowError:
        raise
    except AIError:
        raise
    except httpx.TimeoutException:
        raise AIError("AI 响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise AIError(f"AI 服务异常（{e.response.status_code}）")
    except Exception as e:
        logger.error("AI call failed", exc_info=True)
        raise AIError("AI 调用失败，请稍后重试") from e


def _text_only(content: "str | list") -> str:
    """Extract plain text from multimodal content, for providers without vision support."""
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if b.get("type") == "text")


async def call_ai_stream(system_prompt: str, history: list, user_message: "str | list",
                         provider: str = "deepseek", model: str = "deepseek-chat",
                         temperature: float = 0.7, max_tokens: int = 4096,
                         usage_out: list | None = None):
    """Unified streaming entry point for all providers.

    user_message may be a multimodal content list (OpenAI image_url format);
    non-vision providers fall back to text-only.
    """
    keys = _keys()
    try:
        if provider in ("deepseek", "openai"):
            url = ("https://api.deepseek.com/v1/chat/completions" if provider == "deepseek"
                   else "https://api.openai.com/v1/chat/completions")
            label = "DeepSeek" if provider == "deepseek" else "OpenAI"
            api_key = _require_key(keys, "deepseek" if provider == "deepseek" else "openai", label)
            # DeepSeek doesn't support vision; OpenAI does — keep list for openai, flatten for deepseek
            user_content = user_message if provider == "openai" else _text_only(user_message)
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history[-10:])
            messages.append({"role": "user", "content": user_content})
            async for chunk in _stream_openai_compat(url, api_key, model, messages, temperature, max_tokens, usage_out=usage_out):
                yield chunk

        elif provider == "ollama":
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history[-10:])
            messages.append({"role": "user", "content": _text_only(user_message)})
            async for chunk in _stream_ollama(model, messages, keys["ollama_url"]):
                yield chunk

        elif provider == "claude":
            api_key = _require_key(keys, "anthropic", "Anthropic")
            hist = [m for m in history[-10:] if m["role"] in ("user", "assistant")]
            hist.append({"role": "user", "content": user_message})
            claude_msgs = _to_claude_messages(hist)
            async for chunk in _stream_claude(model, system_prompt, claude_msgs, api_key, temperature, max_tokens, usage_out=usage_out):
                yield chunk

        else:
            raise AIError(f"不支持的模型提供商: {provider}")

    except AIError:
        raise
    except httpx.TimeoutException:
        raise AIError("AI 响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise AIError(f"AI 服务异常（{e.response.status_code}），请检查 API Key 配置")
    except Exception as e:
        logger.error("AI call failed", exc_info=True)
        raise AIError("AI 调用失败，请稍后重试") from e
