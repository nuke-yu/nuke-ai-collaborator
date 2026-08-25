"""CJK-aware token estimation and multimodal content normalization."""
from __future__ import annotations
from contextvars import ContextVar

_PER_MESSAGE_OVERHEAD = 8
_TOKEN_CACHE_MAX = 32
_token_cache: dict[int, tuple[int, int, int, str]] = {}
# Extra token cost assigned to CJK characters over the English 1 token / 4
# characters baseline.  It is deliberately configurable through the explicit
# calibration API below rather than silently assuming a provider tokenizer.
_cjk_adjustment = 2.0
_cjk_adjustments: dict[str, float] = {"default": _cjk_adjustment}
_active_cjk_adjustment: ContextVar[float] = ContextVar("active_cjk_adjustment", default=_cjk_adjustment)


def activate_cjk_calibration(key: str) -> float:
    """Select the calibrated adjustment for one provider/model in this loop."""
    value = float(_cjk_adjustments.get(key, _cjk_adjustments["default"]))
    _active_cjk_adjustment.set(value)
    _token_cache.clear()
    return value


def register_cjk_calibration(key: str, adjustment: float) -> None:
    _cjk_adjustments[str(key)] = max(0.0, float(adjustment))


def _count_cjk(text: str) -> int:
    return sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿'
               or '豈' <= ch <= '﫿' or '\U00020000' <= ch <= '\U0002a6df'
               or '　' <= ch <= '〿' or '぀' <= ch <= 'ヿ' or '가' <= ch <= '힯')


def _content_cjk(content) -> int:
    return 0 if content is None else _count_cjk(content if isinstance(content, str) else str(content))


def _content_chars(content) -> int:
    return 0 if content is None else len(content if isinstance(content, str) else str(content))


def clean_multimodal_content(content) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text") or "")
            elif btype in ("image", "image_url"):
                parts.append("[图片数据]")
            elif btype == "document":
                parts.append("[文档数据]")
            elif btype == "tool_result" and isinstance(block.get("content"), list):
                parts.append(clean_multimodal_content(block["content"]))
            else:
                parts.append(f"[{btype or '未知数据类型'}]")
        return " ".join(p for p in parts if p.strip())
    return str(content)


def _msg_verifier(m: dict) -> str:
    c = m.get("content") or ""
    return c[:32] if isinstance(c, str) else str(c)[:32]


def _message_chars(m: dict) -> int:
    return _content_chars(m.get("content")) + len(m.get("name") or "") + _PER_MESSAGE_OVERHEAD


def _message_cjk(m: dict) -> int:
    return _content_cjk(m.get("content")) + _count_cjk(m.get("name") or "")


def _chars_to_tokens(total_chars: int, cjk_chars: int) -> int:
    return int((total_chars + cjk_chars * _active_cjk_adjustment.get()) // 4)


def calibrate_cjk_estimator(samples, tokenizer) -> dict[str, float | int]:
    """Calibrate the heuristic against a provider-compatible tokenizer.

    ``samples`` is an iterable of representative text strings and ``tokenizer``
    exposes ``encode(text)`` (or is a callable).  The resulting adjustment is
    process-local and should be performed during worker startup/configuration;
    without calibration the conservative default remains active.
    """
    global _cjk_adjustment
    samples = [str(text or "") for text in samples]
    total_cjk = 0
    total_chars = 0
    total_actual = 0
    count = 0
    for text in samples:
        value = str(text or "")
        cjk = _count_cjk(value)
        if not value or cjk == 0:
            continue
        encoded = tokenizer.encode(value) if hasattr(tokenizer, "encode") else tokenizer(value)
        actual = len(encoded)
        total_cjk += cjk
        total_chars += len(value)
        total_actual += actual
        count += 1
    if not count or not total_cjk:
        return {"samples": count, "adjustment": _cjk_adjustment, "mean_abs_error": 0.0}
    # Solve tokens = (chars + cjk * adjustment) / 4 for the observed corpus.
    _cjk_adjustment = max(0.0, (4.0 * total_actual - total_chars) / total_cjk)
    _active_cjk_adjustment.set(_cjk_adjustment)
    register_cjk_calibration("default", _cjk_adjustment)
    errors = []
    for text in samples:
        value = str(text or "")
        if not value or _count_cjk(value) == 0:
            continue
        encoded = tokenizer.encode(value) if hasattr(tokenizer, "encode") else tokenizer(value)
        estimate = int((len(value) + _count_cjk(value) * _cjk_adjustment) // 4)
        errors.append(abs(estimate - len(encoded)))
    _token_cache.clear()
    return {
        "samples": count,
        "adjustment": _cjk_adjustment,
        "mean_abs_error": sum(errors) / len(errors) if errors else 0.0,
    }


def estimate_tokens(messages: list[dict]) -> int:
    key, n = id(messages), len(messages)
    cached = _token_cache.get(key)
    if cached is not None:
        cached_n, cached_chars, cached_cjk, cached_ver = cached
        if cached_n == n and (_msg_verifier(messages[-1]) if n else "") == cached_ver:
            return _chars_to_tokens(cached_chars, cached_cjk)
        if cached_n == n - 1 and (cached_n < 2 or _msg_verifier(messages[-2]) == cached_ver):
            new_chars = cached_chars + _message_chars(messages[-1])
            new_cjk = cached_cjk + _message_cjk(messages[-1])
            _token_cache[key] = (n, new_chars, new_cjk, _msg_verifier(messages[-1]))
            return _chars_to_tokens(new_chars, new_cjk)
    total_chars = sum(_message_chars(m) for m in messages)
    total_cjk = sum(_message_cjk(m) for m in messages)
    if len(_token_cache) >= _TOKEN_CACHE_MAX:
        for old_key in list(_token_cache)[: _TOKEN_CACHE_MAX // 2]:
            del _token_cache[old_key]
    _token_cache[key] = (n, total_chars, total_cjk, _msg_verifier(messages[-1]) if messages else "")
    return _chars_to_tokens(total_chars, total_cjk)
