import logging
import asyncio
from typing import Optional, AsyncGenerator
from ai.client import call_ai_once, call_ai_stream_messages, AIContextOverflowError, AIError
from executors import compact
from executors.base import ExecutionContext

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
    def __init__(self, ctx: ExecutionContext, session_id: str, temp_id: str):
        self.ctx = ctx
        self.session_id = session_id
        self.temp_id = temp_id
        self.usage = AIUsage()

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
                   reinject_fn: Optional[callable] = None) -> dict:
        """Single non-streaming AI call with automatic overflow retry."""
        try:
            res = await call_ai_once(
                system_prompt, messages, provider, model,
                temperature, max_tokens, tools,
                use_cached_microcompact=use_cached_microcompact
            )
            self.usage.add(res.get("usage", {}))
            await self._sync_tokens()
            return res
            
        except AIContextOverflowError:
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
            })
            
            # Retry once after compaction
            res = await call_ai_once(
                system_prompt, messages, provider, model,
                temperature, max_tokens, tools,
                use_cached_microcompact=use_cached_microcompact
            )
            self.usage.add(res.get("usage", {}))
            await self._sync_tokens()
            return res

    async def stream(self,
                     system_prompt: str,
                     messages: list,
                     model: str,
                     provider: str,
                     temperature: float = 0.7,
                     max_tokens: int = 4096,
                     auto_compact: bool = True,
                     reinject_fn: Optional[callable] = None) -> AsyncGenerator[str, None]:
        """Streaming AI call with automatic usage tracking and overflow retry."""
        usage_out = []
        try:
            async for chunk in call_ai_stream_messages(
                system_prompt, messages, provider, model, 
                temperature, max_tokens, usage_out=usage_out
            ):
                yield chunk
            
            if usage_out:
                self.usage.add(usage_out[0])
                await self._sync_tokens()

        except AIContextOverflowError:
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
            })
            
            # Retry streaming
            usage_out.clear()
            async for chunk in call_ai_stream_messages(
                system_prompt, messages, provider, model, 
                temperature, max_tokens, usage_out=usage_out
            ):
                yield chunk
                
            if usage_out:
                self.usage.add(usage_out[0])
                await self._sync_tokens()

    async def _sync_tokens(self):
        """Persist current usage to database via interaction adapter."""
        usage_data = self.usage.to_dict()
        
        # Calculate USD cost
        from ai.pricing import calculate_cost
        cost = calculate_cost(
            self.ctx.bot.get("model_provider", "deepseek"),
            self.ctx.bot.get("model_name", "deepseek-chat"),
            usage_data
        )
        usage_data["cost_usd"] = cost
        
        # Determine current ticket from execution context if available
        # The RDManager could potentially inject this, or we can look it up based on the group's active workflow
        ticket_id = getattr(self.ctx, "active_ticket_id", None)
        if ticket_id:
            usage_data["ticket_id"] = ticket_id
            
        await self.ctx.interaction.update_session_tokens(
            self.session_id, **usage_data
        )
