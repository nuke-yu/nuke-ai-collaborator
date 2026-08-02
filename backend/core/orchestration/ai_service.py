import logging
import asyncio
import inspect
import re
import time
import uuid
from typing import Optional, AsyncGenerator
from ai.client import call_ai_once, call_ai_stream_messages, AIContextOverflowError, AIError
from executors import compact
from executors.base import ExecutionContext
from ai.pricing import calculate_cost

log = logging.getLogger(__name__)

class AIUsage:
    """Helper to track accumulated token usage."""
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0

    def add(self, usage: dict):
        if not usage: return
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.cache_read_tokens += usage.get("cache_read_tokens", 0)
        self.cache_creation_tokens += usage.get("cache_creation_tokens", 0)

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens
        }

class AIService:
    """
    Unified AI Service Layer (Point 4).
    Handles:
    - Token usage accumulation.
    - Automated context compaction on overflow.
    - Standardized error handling & UI broadcasts.
    """
    # Regex patterns to extract thinking/reasoning from model responses
    # DeepSeek uses ｜｜DSML｜｜思考｜｜...｜｜DSML｜｜结束思考｜｜
    # Some models use <thought>...</thought> or ```think</think>
    THINKING_PATTERNS = [
        re.compile(r'[\｜\|]DSML[\｜\|]思考[\｜\|](.+?)[\｜\|]DSML[\｜\|]结束思考[\｜\|]', re.DOTALL),
        re.compile(r'<thought>(.+?)</thought>', re.DOTALL),
        re.compile(r'```think\n?(.+?)\n?```', re.DOTALL),
    ]
    def __init__(self, ctx: ExecutionContext, session_id: str, temp_id: str):
        self.ctx = ctx
        self.session_id = session_id
        self.temp_id = temp_id
        self.usage = AIUsage()
        self._request_ordinal = 0

    def _event_recorder(self):
        """Return a real async Session Event recorder, ignoring loose sync mocks."""
        recorder = getattr(self.ctx.interaction, "append_session_event", None)
        return recorder if callable(recorder) and inspect.iscoroutinefunction(recorder) else None

    async def _start_request(self, provider: str, model: str, *, streaming: bool,
                             operation: str, retry_of: str = "") -> tuple[str | None, float]:
        """Create the durable request row when the interaction supports Session Events."""
        self._request_ordinal += 1
        request_id = f"req_{uuid.uuid4().hex}"
        started = time.monotonic()
        recorder = self._event_recorder()
        if recorder is None:
            return None, started
        await recorder(self.session_id, "model_request_started", {
            "request_id": request_id,
            "request_ordinal": self._request_ordinal,
            "retry_of": retry_of,
            "operation": operation,
            "provider": provider,
            "model": model,
            "streaming": streaming,
            "ticket_id": getattr(self.ctx, "active_ticket_id", None) or "",
        })
        return request_id, started

    async def _finish_request(
        self, request_id: str | None, started: float, provider: str, model: str,
        *, usage: dict | None = None, response_type: str = "", error: BaseException | None = None,
    ) -> None:
        normalized = {
            "input_tokens": int((usage or {}).get("input_tokens") or 0),
            "output_tokens": int((usage or {}).get("output_tokens") or 0),
            "cache_read_tokens": int((usage or {}).get("cache_read_tokens") or 0),
            "cache_creation_tokens": int((usage or {}).get("cache_creation_tokens") or 0),
        }
        if request_id is None:
            if error is None:
                await self._sync_tokens(normalized, provider=provider, model=model)
            return
        payload = {
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            "ticket_id": getattr(self.ctx, "active_ticket_id", None) or "",
            **normalized,
        }
        if error is None:
            payload["response_type"] = response_type
            event_type = "model_request_completed"
        else:
            payload["error_type"] = type(error).__name__
            event_type = "model_request_failed"
        recorder = self._event_recorder()
        if recorder is None:
            if error is None:
                await self._sync_tokens(normalized, provider=provider, model=model)
            return
        await recorder(self.session_id, event_type, payload)

    async def call(self, 
                   system_prompt: str, 
                   messages: list, 
                   model: str,
                   provider: str,
                   temperature: float = 0.7,
                   max_tokens: int = 4096,
                   tools: Optional[list] = None,
                   use_cached_microcompact: bool = False,
                   auto_compact: bool = True,
                   reinject_fn: Optional[callable] = None,
                   operation: str = "inference") -> dict:
        """Single non-streaming AI call with automatic overflow retry."""
        request_id, started = await self._start_request(
            provider, model, streaming=False, operation=operation
        )
        try:
            res = await call_ai_once(
                system_prompt, messages, provider, model,
                temperature, max_tokens, tools,
                use_cached_microcompact=use_cached_microcompact
            )
            call_usage = res.get("usage", {})
            self.usage.add(call_usage)
            await self._finish_request(
                request_id, started, provider, model,
                usage=call_usage, response_type=str(res.get("type") or ""),
            )
            return res
            
        except AIContextOverflowError as overflow:
            await self._finish_request(
                request_id, started, provider, model, error=overflow
            )
            if not auto_compact:
                raise
            
            log.warning("AI Context Overflow detected for session %s, compacting...", self.session_id)
            # Perform compaction
            context_text = await reinject_fn() if reinject_fn else ""
            
            # Note: compact_conversation expects a specific signature
            # We assume it handles the message array update
            new_messages = await compact.compact_conversation(
                messages, system_prompt, provider, model, temperature,
                context_text=context_text
            )
            # Update messages in place or return? tool_loop usually expects them to be updated.
            # For simplicity in this interface, we assume the caller passes the message list and we modify it.
            messages[:] = new_messages
            
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "compaction", "temp_id": self.temp_id,
                "strategy": "overflow_recovery",
                "message": "上下文溢出，已自动压缩并重试",
                "session_id": self.session_id,
            })
            
            # Retry once after compaction
            retry_id, retry_started = await self._start_request(
                provider, model, streaming=False, operation=operation,
                retry_of=request_id or "",
            )
            try:
                res = await call_ai_once(
                    system_prompt, messages, provider, model,
                    temperature, max_tokens, tools,
                    use_cached_microcompact=use_cached_microcompact
                )
                call_usage = res.get("usage", {})
                self.usage.add(call_usage)
                await self._finish_request(
                    retry_id, retry_started, provider, model,
                    usage=call_usage, response_type=str(res.get("type") or ""),
                )
                return res
            except BaseException as exc:
                await self._finish_request(
                    retry_id, retry_started, provider, model, error=exc
                )
                raise
        except BaseException as exc:
            await self._finish_request(
                request_id, started, provider, model, error=exc
            )
            raise

    async def stream(self,
                     system_prompt: str,
                     messages: list,
                     model: str,
                     provider: str,
                     temperature: float = 0.7,
                     max_tokens: int = 4096,
                     auto_compact: bool = True,
                     reinject_fn: Optional[callable] = None,
                     operation: str = "final_response") -> AsyncGenerator[str, None]:
        """Streaming AI call with automatic usage tracking and overflow retry."""
        usage_out = []
        request_id, started = await self._start_request(
            provider, model, streaming=True, operation=operation
        )
        try:
            async for chunk in call_ai_stream_messages(
                system_prompt, messages, provider, model, 
                temperature, max_tokens, usage_out=usage_out
            ):
                yield chunk
            
            if usage_out:
                call_usage = usage_out[0]
                self.usage.add(call_usage)
            else:
                call_usage = {}
            await self._finish_request(
                request_id, started, provider, model,
                usage=call_usage, response_type="text",
            )

        except AIContextOverflowError as overflow:
            await self._finish_request(
                request_id, started, provider, model, error=overflow
            )
            if not auto_compact:
                raise
                
            log.warning("AI Context Overflow (Stream) for session %s, compacting...", self.session_id)
            context_text = await reinject_fn() if reinject_fn else ""
            new_messages = await compact.compact_conversation(
                messages, system_prompt, provider, model, temperature,
                context_text=context_text
            )
            messages[:] = new_messages
            
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "compaction", "temp_id": self.temp_id,
                "strategy": "overflow_recovery",
                "message": "流式回复溢出，已自动压缩并重试",
                "session_id": self.session_id,
            })
            
            # Retry streaming
            usage_out.clear()
            retry_id, retry_started = await self._start_request(
                provider, model, streaming=True, operation=operation,
                retry_of=request_id or "",
            )
            try:
                async for chunk in call_ai_stream_messages(
                    system_prompt, messages, provider, model,
                    temperature, max_tokens, usage_out=usage_out
                ):
                    yield chunk
                call_usage = usage_out[0] if usage_out else {}
                self.usage.add(call_usage)
                await self._finish_request(
                    retry_id, retry_started, provider, model,
                    usage=call_usage, response_type="text",
                )
            except BaseException as exc:
                await self._finish_request(
                    retry_id, retry_started, provider, model, error=exc
                )
                raise
        except BaseException as exc:
            await self._finish_request(
                request_id, started, provider, model, error=exc
            )
            raise

    async def _sync_tokens(self, incremental_usage: dict, *, provider: str, model: str):
        """H-3: Persist incremental call usage to database via interaction adapter."""
        # Calculate USD cost for this specific call
        cost = calculate_cost(
            provider,
            model,
            incremental_usage
        )
        
        usage_to_sync = {**incremental_usage, "cost_usd": cost}
        await self.ctx.interaction.update_session_tokens(
            self.session_id, **usage_to_sync
        )
