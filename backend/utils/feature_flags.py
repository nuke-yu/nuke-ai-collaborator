"""Feature flags system for Nuke AI Collaborator.

Provides runtime feature flag control via environment variables.

Usage:
    # Enable a feature
    export FEATURE_MCP_COLLECTOR=true

    # In code
    if feature("MCP_COLLECTOR"):
        # MCP Collector logic
        pass

Based on Claude Code Haha's feature flag pattern.
"""

import os
from functools import lru_cache
from typing import Dict, Set


# ── Feature Flag Registry ─────────────────────────────────────────────────────

# Built-in feature flags
DEFAULT_FEATURES: Set[str] = {
    "MCP_COLLECTOR",
    "HEADLESS_MODE",
    "SCHEMA_BASED_TOOLS",
    "PLUGIN_HOOKS",
    "WORKER_HEALTH_CHECK",
    "PREFLIGHT_OPTIMIZATION",
}


# ── Feature Flag API ────────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def feature(name: str, default: bool = False) -> bool:
    """
    Check if a feature is enabled.

    Args:
        name: Feature name (uppercase, e.g., "MCP_COLLECTOR")
        default: Default value if not set in environment

    Returns:
        True if feature is enabled, False otherwise

    Environment variable format: FEATURE_<NAME> (uppercase)

    Example:
        if feature("MCP_COLLECTOR"):
            # MCP Collector is enabled
            pass

        if feature("EXPERIMENTAL_TOOL", default=True):
            # Experimental tool enabled by default
            pass
    """
    env_var = f"FEATURE_{name.upper()}"  # Ensure uppercase
    value = os.getenv(env_var)

    if value is None:
        return default

    # Parse the value
    value = value.lower().strip()
    return value in ("true", "1", "yes", "on", "enabled")


def enable_feature(name: str) -> None:
    """
    Enable a feature at runtime (bypasses environment variable).

    Note: This modifies the LRU cache, so use with care in multi-threaded
    environments.

    Args:
        name: Feature name to enable
    """
    # Clear cache for this feature
    feature.cache_clear()

    # Set environment variable
    os.environ[f"FEATURE_{name}"] = "true"

    # Re-check the feature
    _ = feature(name)  # Trigger cache


def disable_feature(name: str) -> None:
    """
    Disable a feature at runtime (bypasses environment variable).

    Note: This modifies the LRU cache, so use with care in multi-threaded
    environments.

    Args:
        name: Feature name to disable
    """
    # Clear cache for this feature
    feature.cache_clear()

    # Set environment variable to false
    os.environ[f"FEATURE_{name}"] = "false"

    # Re-check the feature
    _ = feature(name)  # Trigger cache


def list_enabled_features() -> list[str]:
    """
    List all enabled features.

    Returns:
        List of feature names that are currently enabled
    """
    enabled = []

    for feature_name in DEFAULT_FEATURES:
        if feature(feature_name):
            enabled.append(feature_name)

    # Also check for user-defined features
    for key, value in os.environ.items():
        if key.startswith("FEATURE_") and key not in [f"FEATURE_{f}" for f in DEFAULT_FEATURES]:
            if value.lower() in ("true", "1", "yes", "on", "enabled"):
                feature_name = key[8:]  # Remove "FEATURE_" prefix
                enabled.append(feature_name)

    return sorted(enabled)


def list_available_features() -> list[str]:
    """
    List all available features (including disabled ones).

    Returns:
        List of all known feature names
    """
    available = list(DEFAULT_FEATURES)

    # Also check for user-defined features
    for key in os.environ:
        if key.startswith("FEATURE_"):
            feature_name = key[8:]  # Remove "FEATURE_" prefix
            if feature_name not in available:
                available.append(feature_name)

    return sorted(available)


# ── Conditional Import Helper ────────────────────────────────────────────────

def conditional_import(feature_name: str, true_module: str, false_module: str = None):
    """
    Create a conditional module import based on feature flag.

    Usage:
        get_tools = conditional_import(
            "CONDITIONAL_TOOLS",
            "backend.tools.advanced",
            "backend.tools.basic"
        )
        tools = get_tools()

    Args:
        feature_name: Feature flag name
        true_module: Module to import if feature is enabled
        false_module: Module to import if feature is disabled (optional)

    Returns:
        Module (true_module if enabled, false_module or None if disabled)
    """
    if feature(feature_name):
        try:
            return __import__(true_module, fromlist=[""])
        except ImportError:
            return None
    else:
        if false_module:
            try:
                return __import__(false_module, fromlist=[""])
            except ImportError:
                return None
        return None


# ── Feature Flag Context Manager ─────────────────────────────────────────────

from contextlib import contextmanager


@contextmanager
def temporarily_enable_feature(name: str):
    """
    Temporarily enable a feature within a context block.

    Usage:
        with temporarily_enable_feature("EXPERIMENTAL_FEATURE"):
            # Experimental code runs here
            pass
        # Feature is disabled again here
    """
    # Save current state
    was_enabled = feature(name)

    # Enable the feature
    enable_feature(name)

    try:
        yield
    finally:
        # Restore original state
        if not was_enabled:
            disable_feature(name)


@contextmanager
def temporarily_disable_feature(name: str):
    """
    Temporarily disable a feature within a context block.

    Usage:
        with temporarily_disable_feature("DEBUG_TOOLS"):
            # Normal code runs here
            pass
        # Feature is enabled again here
    """
    # Save current state
    was_enabled = feature(name)

    # Disable the feature
    disable_feature(name)

    try:
        yield
    finally:
        # Restore original state
        if was_enabled:
            enable_feature(name)


# ── Feature Flag Validation ──────────────────────────────────────────────────

class FeatureValidationError(ValueError):
    """Raised when a feature flag is invalid."""
    pass


def validate_feature_name(name: str) -> bool:
    """
    Validate a feature name format.

    Args:
        name: Feature name to validate

    Returns:
        True if valid

    Raises:
        FeatureValidationError: If name is invalid
    """
    if not name:
        raise FeatureValidationError("Feature name cannot be empty")

    # Check for invalid characters (only letters, numbers, underscores allowed)
    invalid_chars = set(name) - set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if invalid_chars:
        raise FeatureValidationError(
            f"Feature name contains invalid characters: {invalid_chars}"
        )

    return True


def get_feature_config(feature_name: str) -> dict:
    """
    Get configuration for a specific feature.

    Usage:
        config = get_feature_config("MCP_COLLECTOR")
        if config.get("enabled"):
            # MCP Collector configuration
            collector_url = config.get("url")
            timeout = config.get("timeout", 30)

    Returns:
        Dict with feature configuration (may be empty)
    """
    # This can be extended to read from config files
    # For now, return empty dict
    return {}


# ── Feature Flag Metrics ────────────────────────────────────────────────────

def get_feature_metrics() -> dict:
    """
    Get metrics about feature flags.

    Returns:
        Dict with feature flag statistics
    """
    enabled = list_enabled_features()
    all_features = list_available_features()

    return {
        "total": len(all_features),
        "enabled": len(enabled),
        "disabled": len(all_features) - len(enabled),
        "enabled_features": enabled,
        "default_features": len([f for f in enabled if f in DEFAULT_FEATURES]),
        "custom_features": len([f for f in enabled if f not in DEFAULT_FEATURES]),
    }


# ── Initialization ───────────────────────────────────────────────────────────

def initialize_feature_flags():
    """
    Initialize feature flags at application startup.

    This function can be called from main.py to ensure all feature flags
    are properly loaded and validated.
    """
    # Validate all environment variables
    for key in os.environ:
        if key.startswith("FEATURE_"):
            feature_name = key[8:]
            try:
                validate_feature_name(feature_name)
            except FeatureValidationError as e:
                import logging
                logging.warning(f"Invalid feature flag name '{key}': {e}")

    # Log enabled features
    enabled = list_enabled_features()
    if enabled:
        import logging
        logging.info(f"Enabled features: {', '.join(enabled)}")

    # Clear cache to ensure fresh state
    feature.cache_clear()


# ── CLI Helper ────────────────────────────────────────────────────────────────

def print_feature_flags():
    """Print all feature flags and their status (for debugging)."""
    import json

    metrics = get_feature_metrics()

    print("Feature Flags:")
    print(f"  Total: {metrics['total']}")
    print(f"  Enabled: {metrics['enabled']}")
    print(f"  Disabled: {metrics['disabled']}")
    print()

    if metrics['enabled_features']:
        print("  Enabled:")
        for name in metrics['enabled_features']:
            env_var = f"FEATURE_{name}"
            value = os.getenv(env_var, "(not set)")
            is_default = " (default)" if name in DEFAULT_FEATURES else ""
            print(f"    {name}: {value}{is_default}")

    disabled = [f for f in metrics['enabled_features'] if f not in metrics['enabled_features']]
    if len(DEFAULT_FEATURES) > len(metrics['enabled_features']):
        print("  Disabled:")
        for name in sorted(DEFAULT_FEATURES - set(metrics['enabled_features'])):
            env_var = f"FEATURE_{name}"
            value = os.getenv(env_var, "(not set)")
            print(f"    {name}: {value}")


if __name__ == "__main__":
    print_feature_flags()
