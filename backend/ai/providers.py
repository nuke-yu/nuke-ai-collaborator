"""Canonical provider/model identity used by execution metadata.

This module deliberately describes capabilities only.  Transport clients, API
keys, retries, and routing remain owned by :mod:`ai.client` and the executor.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.model_limits import _resolve_ceiling
from ai.pricing import PRICING_VERSION


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Stable, serializable description of one provider/model pair."""

    provider_id: str
    model_id: str
    context_window: int | None
    max_output_tokens: int
    supports_tools: bool
    supports_vision: bool
    supports_thinking: bool
    pricing_version: int
    deprecated: bool = False
    replacement_model: str | None = None
    fallback_model: str | None = None

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip().lower()
        model_id = self.model_id.strip()
        if not provider_id or not model_id:
            raise ValueError("provider_id and model_id are required")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("context_window must be positive or None")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.pricing_version <= 0:
            raise ValueError("pricing_version must be positive")
        if self.deprecated and not self.replacement_model and not self.fallback_model:
            raise ValueError("deprecated models must define a replacement or fallback")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "model_id", model_id)


# These are policy-level capabilities, not transport implementation details.
# Unknown models inherit their provider's conservative defaults.
_PROVIDER_DEFAULTS: dict[str, dict[str, object]] = {
    "deepseek": {"context_window": 128_000, "supports_tools": True, "supports_vision": False, "supports_thinking": True},
    "openai": {"context_window": 128_000, "supports_tools": True, "supports_vision": True, "supports_thinking": False},
    "claude": {"context_window": 200_000, "supports_tools": True, "supports_vision": True, "supports_thinking": True},
    "minimax": {"context_window": None, "supports_tools": True, "supports_vision": False, "supports_thinking": False},
    "qwen": {"context_window": None, "supports_tools": True, "supports_vision": False, "supports_thinking": False},
    "zhipu": {"context_window": None, "supports_tools": True, "supports_vision": False, "supports_thinking": False},
    "ollama": {"context_window": None, "supports_tools": True, "supports_vision": False, "supports_thinking": False},
}


class ProviderRegistry:
    """Small in-process registry for canonical provider/model descriptors."""

    def __init__(self) -> None:
        self._descriptors: dict[tuple[str, str], ProviderDescriptor] = {}

    def register(self, descriptor: ProviderDescriptor) -> None:
        key = (descriptor.provider_id, descriptor.model_id)
        existing = self._descriptors.get(key)
        if existing is not None and existing != descriptor:
            raise ValueError(f"descriptor already registered: {key[0]}/{key[1]}")
        self._descriptors[key] = descriptor

    def resolve(self, provider_id: str, model_id: str) -> ProviderDescriptor:
        provider = (provider_id or "").strip().lower()
        model = (model_id or "").strip()
        if not provider or not model:
            raise ValueError("provider_id and model_id are required")

        exact = self._descriptors.get((provider, model))
        if exact is not None:
            return exact

        candidates = [
            descriptor for (candidate_provider, family), descriptor in self._descriptors.items()
            if candidate_provider == provider and family != "_default" and family in model.lower()
        ]
        if candidates:
            family = max(candidates, key=lambda item: len(item.model_id))
            # Preserve the concrete model requested by the caller.  The family
            # descriptor supplies capabilities; it must not rewrite the model
            # identity that will be written to the usage ledger.
            return ProviderDescriptor(
                provider_id=provider,
                model_id=model,
                context_window=family.context_window,
                max_output_tokens=family.max_output_tokens,
                supports_tools=family.supports_tools,
                supports_vision=family.supports_vision,
                supports_thinking=family.supports_thinking,
                pricing_version=family.pricing_version,
                deprecated=family.deprecated,
                replacement_model=family.replacement_model,
                fallback_model=family.fallback_model,
            )

        defaults = _PROVIDER_DEFAULTS.get(provider, {})
        return ProviderDescriptor(
            provider_id=provider,
            model_id=model,
            context_window=defaults.get("context_window"),
            max_output_tokens=_resolve_ceiling(provider, model),
            supports_tools=bool(defaults.get("supports_tools", False)),
            supports_vision=bool(defaults.get("supports_vision", False)),
            supports_thinking=bool(defaults.get("supports_thinking", False)),
            pricing_version=PRICING_VERSION,
        )

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._descriptors.values())


def _build_default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    for provider, models in {
        "deepseek": ("deepseek-reasoner", "deepseek-chat"),
        "openai": ("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"),
        "claude": ("haiku", "sonnet", "opus-4-5", "opus"),
    }.items():
        defaults = _PROVIDER_DEFAULTS[provider]
        for model in models:
            registry.register(ProviderDescriptor(
                provider_id=provider,
                model_id=model,
                context_window=defaults["context_window"],
                max_output_tokens=_resolve_ceiling(provider, model),
                supports_tools=bool(defaults["supports_tools"]),
                supports_vision=bool(defaults["supports_vision"]),
                supports_thinking=bool(defaults["supports_thinking"]),
                pricing_version=PRICING_VERSION,
            ))
    return registry


provider_registry = _build_default_registry()


def resolve_provider_descriptor(provider_id: str, model_id: str) -> ProviderDescriptor:
    """Resolve an exact, family, or conservative provider/model descriptor."""
    return provider_registry.resolve(provider_id, model_id)


class ProviderGovernanceError(ValueError):
    """The selected model cannot satisfy an execution governance rule."""


def enforce_provider_governance(
    descriptor: ProviderDescriptor,
    *,
    require_tools: bool = False,
    require_vision: bool = False,
    require_thinking: bool = False,
    estimated_cost_usd: float = 0.0,
    budget_usd: float | None = None,
) -> None:
    """Validate capabilities and budget before a Worker starts inference."""
    if descriptor.deprecated:
        replacement = descriptor.replacement_model or descriptor.fallback_model or ""
        raise ProviderGovernanceError(
            f"model {descriptor.provider_id}/{descriptor.model_id} is deprecated; use {replacement}"
        )
    requirements = (
        (require_tools, descriptor.supports_tools, "tool calling"),
        (require_vision, descriptor.supports_vision, "vision"),
        (require_thinking, descriptor.supports_thinking, "thinking"),
    )
    for required, supported, label in requirements:
        if required and not supported:
            raise ProviderGovernanceError(
                f"model {descriptor.provider_id}/{descriptor.model_id} does not support {label}"
            )
    if budget_usd is not None and estimated_cost_usd > budget_usd:
        raise ProviderGovernanceError(
            f"estimated model cost {estimated_cost_usd:.6f} exceeds budget {budget_usd:.6f}"
        )
