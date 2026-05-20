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

async def call_ai(system_prompt: str, history: list, user_message: str) -> str:
    """Non-streaming call (DeepSeek only, used for legacy/non-streaming paths)"""
    api_key = _keys()["deepseek"]
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "deepseek-chat", "messages": messages, "temperature": 0.7, "max_tokens": 4096},
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        raise AIError("AI 响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise AIError(f"AI 服务异常（{e.response.status_code}）")
    except Exception as e:
        raise AIError(f"AI 调用失败：{str(e)}")

async def _stream_openai_compat(url: str, api_key: str, model: str, messages: list):
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 4096, "stream": True},
        ) as response:
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

async def _stream_claude(model: str, system_prompt: str, messages: list, api_key: str = ""):
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
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": messages,
                "stream": True,
            },
        ) as response:
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

async def call_ai_stream(system_prompt: str, history: list, user_message: str,
                         provider: str = "deepseek", model: str = "deepseek-chat"):
    """Unified streaming entry point for all providers."""
    keys = _keys()
    try:
        if provider in ("deepseek", "openai"):
            url = ("https://api.deepseek.com/v1/chat/completions" if provider == "deepseek"
                   else "https://api.openai.com/v1/chat/completions")
            api_key = keys["deepseek"] if provider == "deepseek" else keys["openai"]
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history[-10:])
            messages.append({"role": "user", "content": user_message})
            async for chunk in _stream_openai_compat(url, api_key, model, messages):
                yield chunk

        elif provider == "ollama":
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history[-10:])
            messages.append({"role": "user", "content": user_message})
            async for chunk in _stream_ollama(model, messages, keys["ollama_url"]):
                yield chunk

        elif provider == "claude":
            hist = [m for m in history[-10:] if m["role"] in ("user", "assistant")]
            hist.append({"role": "user", "content": user_message})
            async for chunk in _stream_claude(model, system_prompt, hist, keys["anthropic"]):
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
