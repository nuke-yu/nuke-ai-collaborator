"""ai/model_limits.py — 每个模型的最大输出 token 上限（与 ai/pricing.py 同构）。

不同模型单次能输出的 token 上限差别很大（deepseek ~8K、Claude 几万）。把它做成一张
provider→model→上限 的表，解析逻辑（精确 → 家族子串最长匹配 → _default）照搬 pricing。

用途：解析 bot 实际该用的 max_tokens —— bot 没显式配就用该模型上限做默认，配了也 clamp
到上限（超过 API 真实上限对部分 provider 会直接报错）。数值为各家公开上限的保守初值，可调。
"""

# provider -> model(家族) -> 最大输出 tokens
MAX_OUTPUT_TOKENS: dict[str, dict[str, int]] = {
    "deepseek": {
        "deepseek-reasoner": 8192,
        "deepseek-chat":     8192,
        "_default":          8192,
    },
    "openai": {
        "gpt-4o-mini": 16384,
        "gpt-4o":      16384,
        "gpt-4.1":     32768,
        "_default":    16384,
    },
    "claude": {
        "haiku":  8192,
        "sonnet": 64000,
        "opus":   32000,
        "_default": 8192,
    },
}

# 表里没有该 provider 时的兜底上限。
_GLOBAL_DEFAULT = 8192


def _resolve_ceiling(provider: str, model: str) -> int:
    provider = (provider or "").lower()
    model = (model or "").lower()
    table = MAX_OUTPUT_TOKENS.get(provider)
    if not table:
        return _GLOBAL_DEFAULT
    if model in table:
        return table[model]
    # 家族匹配：取在 model 名里出现的、最长的 key（"gpt-4.1" 胜过更短的）。
    candidates = [k for k in table if k != "_default" and k in model]
    if candidates:
        return table[max(candidates, key=len)]
    return table.get("_default", _GLOBAL_DEFAULT)


def resolve_max_tokens(provider: str, model: str, configured: int | None = None) -> int:
    """该 bot 本次实际该用的 max_tokens。

    configured 为空（None/0）→ 用模型上限做默认；
    configured 有值        → 尊重但 clamp 到模型上限。
    """
    ceiling = _resolve_ceiling(provider, model)
    if not configured:
        return ceiling
    return min(int(configured), ceiling)
