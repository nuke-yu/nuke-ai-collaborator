import asyncio
import json
import httpx
from config import get_key

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


_AI_RETRY_MAX = 3


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

async def get_embedding(text: str) -> list:
    api_key = _keys()["deepseek"]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.deepseek.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "text-embedding-v2", "input": text[:2000], "encoding_format": "float"},
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]

async def call_ai(system_prompt: str, history: list, user_message: str,
                  temperature: float = 0.7, max_tokens: int = 4096) -> str:
    """Non-streaming call (DeepSeek only, used for legacy/non-streaming paths)"""
    keys = _keys()
    api_key = _require_key(keys, "deepseek", "DeepSeek")
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "deepseek-chat", "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        raise AIError("AI 响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise AIError(f"AI 服务异常（{e.response.status_code}）")
    except Exception as e:
        raise AIError(f"AI 调用失败：{str(e)}")

async def _stream_openai_compat(url: str, api_key: str, model: str, messages: list,
                                temperature: float = 0.7, max_tokens: int = 4096):
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": True},
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
                    delta = json.loads(data_str)["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

async def _stream_ollama(model: str, messages: list, base_url: str = "http://localhost:11434"):
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", f"{base_url}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
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
                         temperature: float = 0.7, max_tokens: int = 4096):
    async with httpx.AsyncClient(timeout=120) as client:
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
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {}).get("text", "")
                        if delta:
                            yield delta
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
            raw_result.append({"role": role, "content": m.get("content", "")})

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


async def _once_openai_compat(url: str, api_key: str, model: str, system_prompt: str,
                               messages: list, temperature: float, max_tokens: int,
                               tools: list | None) -> dict:
    full_msgs = [{"role": "system", "content": system_prompt}] + messages
    body = {"model": model, "messages": full_msgs, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=body)
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
    if choice.get("finish_reason") == "tool_calls" and msg.get("tool_calls"):
        calls = [
            {"id": tc["id"], "name": tc["function"]["name"],
             "arguments": json.loads(tc["function"]["arguments"])}
            for tc in msg["tool_calls"]
        ]
        return {"type": "tool_calls", "calls": calls, "assistant_message": msg}
    return {"type": "text", "content": msg.get("content", "")}


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
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
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
    if tool_uses:
        assistant_msg = {
            "role": "assistant", "content": None,
            "tool_calls": [
                {"id": u["id"], "type": "function",
                 "function": {"name": u["name"], "arguments": json.dumps(u["arguments"])}}
                for u in tool_uses
            ],
        }
        return {"type": "tool_calls", "calls": tool_uses, "assistant_message": assistant_msg}
    return {"type": "text", "content": "".join(text_parts)}


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
                raise AIError(f"AI 调用失败：{str(e)}")

    raise AIError(f"API 限流，已重试 {_AI_RETRY_MAX} 次仍失败") from last_error


async def call_ai_stream_messages(
    system_prompt: str,
    messages: list[dict],
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int = 4096,
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
            async for chunk in _stream_openai_compat(url, api_key, model, full_msgs, temperature, max_tokens):
                yield chunk
        elif provider == "claude":
            api_key = _require_key(keys, "anthropic", "Anthropic")
            claude_msgs = _to_claude_messages(messages)
            async for chunk in _stream_claude(model, system_prompt, claude_msgs, api_key, temperature, max_tokens):
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
        raise AIError(f"AI 调用失败：{str(e)}")


async def call_ai_stream(system_prompt: str, history: list, user_message: str,
                         provider: str = "deepseek", model: str = "deepseek-chat",
                         temperature: float = 0.7, max_tokens: int = 4096):
    """Unified streaming entry point for all providers."""
    keys = _keys()
    try:
        if provider in ("deepseek", "openai"):
            url = ("https://api.deepseek.com/v1/chat/completions" if provider == "deepseek"
                   else "https://api.openai.com/v1/chat/completions")
            label = "DeepSeek" if provider == "deepseek" else "OpenAI"
            api_key = _require_key(keys, "deepseek" if provider == "deepseek" else "openai", label)
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history[-10:])
            messages.append({"role": "user", "content": user_message})
            async for chunk in _stream_openai_compat(url, api_key, model, messages, temperature, max_tokens):
                yield chunk

        elif provider == "ollama":
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history[-10:])
            messages.append({"role": "user", "content": user_message})
            async for chunk in _stream_ollama(model, messages, keys["ollama_url"]):
                yield chunk

        elif provider == "claude":
            api_key = _require_key(keys, "anthropic", "Anthropic")
            hist = [m for m in history[-10:] if m["role"] in ("user", "assistant")]
            hist.append({"role": "user", "content": user_message})
            claude_msgs = _to_claude_messages(hist)
            async for chunk in _stream_claude(model, system_prompt, claude_msgs, api_key, temperature, max_tokens):
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
        raise AIError(f"AI 调用失败：{str(e)}")
