# Trigger keywords per role "family" (EN + ZH). A bot auto-responds (no @mention /
# active-bot lock needed) when the message mentions any keyword of its family.
_FAMILY_KEYWORDS = {
    "qa":  ("test", "qa", "bug", "regression", "测试", "质量", "用例", "验证", "缺陷"),
    "ba":  ("requirement", "req", "spec", "feature", "story", "scope", "acceptance",
            "需求", "规格", "要求", "产品", "功能", "验收", "用户故事", "范围"),
    "dev": ("code", "implement", "api", "refactor", "deploy", "feature", "develop",
            "开发", "代码", "实现", "接口", "架构", "前端", "后端", "部署", "重构"),
}


def _role_family(role: str):
    """Map a free-form role string (EN or ZH, any case) to a known family, or None."""
    r = (role or "").strip().lower()
    if any(s in r for s in ("qa", "test", "quality", "测试", "质量")):
        return "qa"          # check QA first so "QA Engineer" isn't caught by "engineer"
    if r == "ba" or any(s in r for s in ("business", "analyst", "product", "pm",
                                          "需求", "产品", "业务", "需求分析")):
        return "ba"
    if any(s in r for s in ("dev", "engineer", "developer", "code", "fullstack",
                            "full-stack", "frontend", "backend", "开发", "工程",
                            "前端", "后端")):
        return "dev"
    return None


def should_bot_respond(message: str, bot_name: str, bot_role: str) -> bool:
    msg_lower = (message or "").lower()
    if "@all" in msg_lower:
        return True
    if f"@{(bot_name or '').lower()}" in msg_lower:
        return True
    family = _role_family(bot_role)
    if not family:
        return False
    return any(kw in msg_lower for kw in _FAMILY_KEYWORDS[family])

def build_image_content(text: str, file_url: str | None, file_type: str | None,
                        provider: str) -> "str | list":
    """Return multimodal content (OpenAI image_url format) for vision providers; text fallback otherwise."""
    if not file_url or not (file_type or "").startswith("image/"):
        return text
    if provider in ("openai", "claude"):
        return [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": file_url}},
        ]
    return f"{text}\n[附图：{file_url}]"


def build_context_message(message: str, sender_name: str, recent_messages: list, is_workflow: bool = False) -> tuple:
    from core.config import TOOL_RESULT_MAX_CHARS

    history_source = recent_messages
    if history_source and history_source[-1].get("content") == message and history_source[-1].get("sender_name") == sender_name:
        history_source = history_source[:-1]

    history = []
    
    if is_workflow and len(history_source) > 8:
        # Keep the trigger message (index 0) in full
        # Keep the last 6 messages in full
        # Intermediate messages (indices 1 to len-7) are compressed
        for idx in range(len(history_source)):
            msg = history_source[idx]
            role = "assistant" if msg["sender_type"] == "bot" else "user"
            content = msg["content"] or ""
            
            # Check if this is an intermediate message that needs compression
            if idx > 0 and idx < len(history_source) - 6:
                if len(content) > 150:
                    content = content[:150] + "\n[... (此期发言已由系统自动压缩以节省上下文空间) ...]"
            
            # Standard truncation for ultra-long content
            if len(content) > TOOL_RESULT_MAX_CHARS:
                half = TOOL_RESULT_MAX_CHARS // 2
                head = content[:half]
                tail = content[-half:]
                truncated_chars = len(content) - (len(head) + len(tail))
                content = f"{head}\n\n[... 历史消息超长已自动截断 {truncated_chars:,} 字符 ...]\n\n{tail}"
                
            history.append({
                "role": role,
                "content": f"[{msg['sender_name']}]: {content}"
            })
    else:
        # Default non-workflow behavior: keep the last 8 messages
        for msg in history_source[-8:]:
            role = "assistant" if msg["sender_type"] == "bot" else "user"
            content = msg["content"]
            if content and isinstance(content, str) and len(content) > TOOL_RESULT_MAX_CHARS:
                half = TOOL_RESULT_MAX_CHARS // 2
                head = content[:half]
                tail = content[-half:]
                truncated_chars = len(content) - (len(head) + len(tail))
                content = f"{head}\n\n[... 历史消息超长已自动截断 {truncated_chars:,} 字符 ...]\n\n{tail}"

            history.append({
                "role": role,
                "content": f"[{msg['sender_name']}]: {content}"
            })
    
    if message and len(message) > TOOL_RESULT_MAX_CHARS:
        half = TOOL_RESULT_MAX_CHARS // 2
        head = message[:half]
        tail = message[-half:]
        truncated_chars = len(message) - (len(head) + len(tail))
        message = f"{head}\n\n[... 消息超长已自动截断 {truncated_chars:,} 字符 ...]\n\n{tail}"

    user_message = f"[{sender_name}]: {message}"
    return history, user_message

