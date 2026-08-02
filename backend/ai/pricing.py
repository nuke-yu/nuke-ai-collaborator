"""
ai/pricing.py — USD cost estimation from token usage.

Computed on the fly (never stored): given a provider + model + the four token
counts we already track, return an estimated USD cost. Prices are per 1M tokens
and are approximate list prices — update PRICING when providers change rates.

Provider cache accounting differs:
  - deepseek / openai : `input_tokens` (prompt_tokens) ALREADY INCLUDES the
    cache-hit tokens, so full-rate input = input_tokens - cache_read_tokens.
  - claude            : `input_tokens` EXCLUDES cache, so they are billed
    separately (read at a discount, creation/write at a premium).
  - ollama            : local, free.
"""

# Rates in USD per 1,000,000 tokens.
PRICING_VERSION = 1

#   input        — full-price prompt tokens
#   output       — completion tokens
#   cache_read   — tokens served from cache (discounted)
#   cache_write  — tokens written to cache (Claude only; 0 elsewhere)
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "deepseek": {
        "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_read": 0.14, "cache_write": 0.0},
        "deepseek-chat":     {"input": 0.27, "output": 1.10, "cache_read": 0.07, "cache_write": 0.0},
        "_default":          {"input": 0.27, "output": 1.10, "cache_read": 0.07, "cache_write": 0.0},
    },
    "openai": {
        "gpt-4o-mini":  {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0.0},
        "gpt-4o":       {"input": 2.50, "output": 10.0, "cache_read": 1.25,  "cache_write": 0.0},
        "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cache_read": 0.10,  "cache_write": 0.0},
        "gpt-4.1":      {"input": 2.00, "output": 8.00, "cache_read": 0.50,  "cache_write": 0.0},
        "_default":     {"input": 2.50, "output": 10.0, "cache_read": 1.25,  "cache_write": 0.0},
    },
    "claude": {
        "haiku":    {"input": 1.0,  "output": 5.0,  "cache_read": 0.10, "cache_write": 1.25},
        "sonnet":   {"input": 3.0,  "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
        "opus-4-5": {"input": 5.0,  "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "opus":     {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
        "_default": {"input": 3.0,  "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    },
}

# Providers whose reported input/prompt token count already folds in cache-hit
# tokens — for these we subtract cache_read to avoid double-charging.
_CACHE_FOLDED_INTO_INPUT = frozenset({"deepseek", "openai"})


def _resolve_rates(provider: str, model: str) -> dict[str, float] | None:
    """Find the rate dict for a provider+model, falling back to family / default."""
    provider = (provider or "").lower()
    model = (model or "").lower()
    table = PRICING.get(provider)
    if not table:
        return None
    if model in table:
        return table[model]
    # Family match: pick the longest key that appears in the model name
    # (so "opus-4-5" wins over "opus").
    candidates = [k for k in table if k != "_default" and k in model]
    if candidates:
        return table[max(candidates, key=len)]
    return table.get("_default")


def calculate_cost(provider: str, model: str, usage: dict) -> float:
    """Estimate USD cost for one usage record.

    usage may contain: input_tokens, output_tokens, cache_read_tokens,
    cache_creation_tokens (any missing/None treated as 0). Unknown providers
    (e.g. ollama) or models with no rate return 0.0.
    """
    rates = _resolve_rates(provider, model)
    if not rates:
        return 0.0

    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    cache_read = usage.get("cache_read_tokens") or 0
    cache_write = usage.get("cache_creation_tokens") or 0

    if (provider or "").lower() in _CACHE_FOLDED_INTO_INPUT:
        full_input = max(0, inp - cache_read)
    else:
        full_input = inp

    return (
        full_input  / 1_000_000 * rates["input"]
        + out       / 1_000_000 * rates["output"]
        + cache_read  / 1_000_000 * rates["cache_read"]
        + cache_write / 1_000_000 * rates["cache_write"]
    )
