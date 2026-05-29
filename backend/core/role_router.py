def should_bot_respond(message: str, bot_name: str, bot_role: str) -> bool:
    msg_lower = message.lower()
    name_lower = bot_name.lower()

    if "@all" in msg_lower:
        return True

    if f"@{name_lower}" in msg_lower:
        return True

    role_keywords = {
        "需求分析师": ["需求", "分析", "要求", "规格", "产品"],
        "开发工程师": ["开发", "代码", "实现", "编程", "接口", "架构"],
        "测试工程师": ["测试", "质量", "用例", "验证", "缺陷"],
    }

    keywords = role_keywords.get(bot_role, [])
    return any(kw in msg_lower for kw in keywords)

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


def build_context_message(message: str, sender_name: str, recent_messages: list) -> tuple:
    history_source = recent_messages
    if history_source and history_source[-1].get("content") == message and history_source[-1].get("sender_name") == sender_name:
        history_source = history_source[:-1]

    history = []
    for msg in history_source[-8:]:
        role = "assistant" if msg["sender_type"] == "bot" else "user"
        history.append({
            "role": role,
            "content": f"[{msg['sender_name']}]: {msg['content']}"
        })
    user_message = f"[{sender_name}]: {message}"
    return history, user_message

