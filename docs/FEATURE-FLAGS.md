# Feature Flags User Guide

## Overview

Feature flags provide runtime control over Nuke AI Collaborator features via environment variables. Based on Claude Code Haha's `feature('NAME')` pattern.

## Quick Start

### Enable a Feature

```bash
# Set environment variable
export FEATURE_MCP_COLLECTOR=true

# Or inline
FEATURE_MCP_COLLECTOR=true python -m backend.main
```

### Check Feature Status

```bash
# List all feature flags
python -m backend.utils.feature_flags
```

### In Code

```python
from backend.utils import feature_flags

# Check if feature is enabled
if feature_flags.feature("MCP_COLLECTOR"):
    # MCP Collector logic
    pass

# With default value
if feature_flags.feature("EXPERIMENTAL_TOOL", default=True):
    # Experimental tool enabled by default
    pass
```

## Environment Variable Format

**Format:** `FEATURE_<NAME>` (uppercase with underscores)

**Examples:**
```bash
export FEATURE_MCP_COLLECTOR=true
export FEATURE_HEADLESS_MODE=true
export FEATURE_SCHEMA_BASED_TOOLS=true
```

## Enabled Value Variants

The following values are treated as **enabled**:
- `true`, `True`, `TRUE`
- `1`
- `yes`, `Yes`, `YES`
- `on`, `On`
- `enabled`

The following values are treated as **disabled**:
- `false`, `False`, `FALSE`
- `0`
- `no`, `No`, `NO`
- `off`
- `disabled`
- Empty string `""`

## Built-in Feature Flags

The following features are built-in and can be enabled/disabled:

| Feature | Description | Default |
|---------|-------------|---------|
| `MCP_COLLECTOR` | Use dedicated MCP Collector process | false |
| `HEADLESS_MODE` | Enable headless mode | true |
| `SCHEMA_BASED_TOOLS` | Use schema-based tool definitions | false |
| `PLUGIN_HOOKS` | Enable plugin hooks (before/after) | false |
| `WORKER_HEALTH_CHECK` | Enable worker health monitoring | false |
| `PREFLIGHT_OPTIMIZATION` | Enable preflight startup optimization | false |

### Example: Enable MCP Collector

```bash
# Enable MCP Collector
export FEATURE_MCP_COLLECTOR=true

# Start the application
python -m backend.main
```

## API Reference

### `feature(name, default=False)`

Check if a feature is enabled.

**Parameters:**
- `name` (str): Feature name (e.g., `"MCP_COLLECTOR"`)
- `default` (bool): Default value if not set (default: `False`)

**Returns:**
- `bool`: `True` if enabled, `False` otherwise

**Example:**
```python
from backend.utils import feature_flags

# Check built-in feature
if feature_flags.feature("MCP_COLLECTOR"):
    print("MCP Collector is enabled")

# Check with default
if feature_flags.feature("EXPERIMENTAL_FEATURE", default=True):
    print("Experimental feature enabled")
```

### `enable_feature(name)`

Enable a feature at runtime.

**Parameters:**
- `name` (str): Feature name to enable

**Example:**
```python
from backend.utils import feature_flags

# Enable feature
feature_flags.enable_feature("DYNAMIC_FEATURE")

# Check if enabled
if feature_flags.feature("DYNAMIC_FEATURE"):
    print("Feature is now enabled")
```

### `disable_feature(name)`

Disable a feature at runtime.

**Parameters:**
- `name` (str): Feature name to disable

**Example:**
```python
from backend.utils import feature_flags

# Disable feature
feature_flags.disable_feature("DEBUG_TOOLS")
```

### `list_enabled_features()`

List all currently enabled features.

**Returns:**
- `list[str]`: List of enabled feature names

**Example:**
```python
from backend.utils import feature_flags

enabled = feature_flags.list_enabled_features()
print(f"Enabled features: {', '.join(enabled)}")
```

### `list_available_features()`

List all available features (including disabled ones).

**Returns:**
- `list[str]`: List of all known feature names

**Example:**
```python
from backend.utils import feature_flags

available = feature_flags.list_available_features()
print(f"Available features: {', '.join(available)}")
```

### `get_feature_metrics()`

Get metrics about feature flags.

**Returns:**
- `dict`: Metrics including total, enabled, disabled counts

**Example:**
```python
from backend.utils import feature_flags

metrics = feature_flags.get_feature_metrics()
print(f"Total: {metrics['total']}")
print(f"Enabled: {metrics['enabled']}")
print(f"Disabled: {metrics['disabled']}")
print(f"Enabled features: {metrics['enabled_features']}")
```

### `conditional_import(feature_name, true_module, false_module=None)`

Conditionally import a module based on feature flag.

**Parameters:**
- `feature_name` (str): Feature flag name
- `true_module` (str): Module to import if enabled
- `false_module` (str): Fallback module if disabled (optional)

**Returns:**
- `module`: Imported module or `None`

**Example:**
```python
from backend.utils import feature_flags

# Import based on feature
if feature_flags.feature("ADVANCED_TOOLS"):
    from backend.tools import advanced
    tools = advanced.get_tools()
else:
    from backend.tools import basic
    tools = basic.get_tools()

# Or using conditional_import
get_tools = feature_flags.conditional_import(
    "ADVANCED_TOOLS",
    "backend.tools.advanced",
    "backend.tools.basic"
)
tools = get_tools()
```

### `initialize_feature_flags()`

Initialize feature flags at application startup.

Called automatically in `main.py`, but can be called manually:

**Example:**
```python
from backend.utils import feature_flags

# Initialize feature flags
feature_flags.initialize_feature_flags()

# Check status
print(feature_flags.list_enabled_features())
```

## Context Managers

### `temporarily_enable_feature(name)`

Temporarily enable a feature within a context block.

**Example:**
```python
from backend.utils import feature_flags

# Feature is disabled by default
assert feature_flags.feature("EXPERIMENTAL") is False

with feature_flags.temporarily_enable_feature("EXPERIMENTAL"):
    # Feature is enabled here
    assert feature_flags.feature("EXPERIMENTAL") is True

# Feature is disabled again here
assert feature_flags.feature("EXPERIMENTAL") is False
```

### `temporarily_disable_feature(name)`

Temporarily disable a feature within a context block.

**Example:**
```python
from backend.utils import feature_flags

# Feature is enabled by default
assert feature_flags.feature("DEBUG_MODE") is True

with feature_flags.temporarily_disable_feature("DEBUG_MODE"):
    # Feature is disabled here
    assert feature_flags.feature("DEBUG_MODE") is False

# Feature is enabled again here
assert feature_flags.feature("DEBUG_MODE") is True
```

## Usage Examples

### Example 1: Conditional Tool Loading

```python
from backend.utils import feature_flags

def get_tools():
    if feature_flags.feature("MCP_COLLECTOR"):
        # Load MCP tools
        from backend.tools import mcp
        return mcp.get_tools()
    else:
        # Load standard tools
        from backend.tools import standard
        return standard.get_tools()
```

### Example 2: Feature-Gated Configuration

```python
from backend.utils import feature_flags

def load_config():
    config = {}

    if feature_flags.feature("MCP_COLLECTOR"):
        config["mcp"] = {
            "enabled": True,
            "collector_addr": os.getenv("MCP_COLLECTOR_ADDR"),
        }

    if feature_flags.feature("HEADLESS_MODE"):
        config["headless"] = {
            "enabled": True,
            "timeout": int(os.getenv("HEADLESS_TIMEOUT", 300000)),
        }

    return config
```

### Example 3: Runtime Feature Toggle

```python
from backend.utils import feature_flags

def toggle_debug_mode(enable):
    """Toggle debug mode at runtime."""
    if enable:
        feature_flags.enable_feature("DEBUG_MODE")
    else:
        feature_flags.disable_feature("DEBUG_MODE")

    print(f"Debug mode: {'enabled' if enable else 'disabled'}")
```

## CLI Usage

### List All Feature Flags

```bash
python -m backend.utils.feature_flags
```

**Output:**
```
Feature Flags:
  Total: 6
  Enabled: 1
  Disabled: 5

  Enabled:
    HEADLESS_MODE: true (default)

  Disabled:
    MCP_COLLECTOR: (not set)
    PLUGIN_HOOKS: (not set)
    PREFLIGHT_OPTIMIZATION: (not set)
    SCHEMA_BASED_TOOLS: (not set)
    WORKER_HEALTH_CHECK: (not set)
```

### Check Specific Feature

```bash
# Using Python
python -c "from backend.utils import feature_flags; print(feature_flags.feature('MCP_COLLECTOR'))"
```

## Best Practices

### 1. Use Uppercase with Underscores

```python
# ✅ Good
feature_flags.feature("MCP_COLLECTOR")
feature_flags.feature("HEADLESS_MODE")

# ❌ Bad
feature_flags.feature("mcp_collector")  # lowercase
feature_flags.feature("mcp-collector")  # hyphen
```

### 2. Set Defaults for Built-in Features

```python
# ✅ Good - Built-in features have defaults
if feature_flags.feature("HEADLESS_MODE"):  # default=True
    pass

# ❌ Bad - Always check env var
if feature_flags.feature("CUSTOM_FEATURE"):  # default=False
    pass
```

### 3. Use Context Managers for Testing

```python
def test_with_feature():
    with feature_flags.temporarily_enable_feature("TEST_FEATURE"):
        # Test code here
        pass
    # Feature automatically disabled
```

### 4. Initialize at Startup

```python
# In main.py
from backend.utils import feature_flags

@asynccontextmanager
async def lifespan(app: FastAPI):
    feature_flags.initialize_feature_flags()  # Call first
    # ... rest of initialization
```

## CI/CD Integration

### GitHub Actions

```yaml
jobs:
  test:
    strategy:
      matrix:
        feature: [MCP_COLLECTOR, HEADLESS_MODE, SCHEMA_BASED_TOOLS]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Test with feature
        run: |
          FEATURE_${{ matrix.feature }}=true python -m pytest
```

### Docker

```dockerfile
# Dockerfile
ARG FEATURE_MCP_COLLECTOR=true
ENV FEATURE_MCP_COLLECTOR=${FEATURE_MCP_COLLECTOR}

# Build with feature
# docker build --build-arg FEATURE_MCP_COLLECTOR=true -t app .
```

## Troubleshooting

### Feature Not Working

**Problem:** Feature flag not being recognized.

**Solution:**
1. Check environment variable name (must be uppercase with underscores)
2. Verify value is one of the enabled values
3. Clear Python cache: `find . -name "*.pyc" -delete`
4. Restart application

### Multiple Values Conflict

**Problem:** Multiple features enabled unexpectedly.

**Solution:**
```bash
# Check current values
env | grep FEATURE_

# Reset to clean state
unset FEATURE_*
export FEATURE_MCP_COLLECTOR=true
```

### Cache Issues

**Problem:** Feature state not updating after change.

**Solution:**
```python
# Clear feature flag cache
from backend.utils import feature_flags
feature_flags.feature.cache_clear()

# Or restart the application
```

## Migration Guide

### From Environment Variables

```python
# Old way (manual string check)
if os.getenv("FEATURE_MCP_COLLECTOR") == "true":
    pass

# New way (use feature flags)
from backend.utils import feature_flags
if feature_flags.feature("MCP_COLLECTOR"):
    pass
```

### From Config File

```python
# Old way (read from config file)
config = read_config()
if config.get("mcp_collector", False):
    pass

# New way (use environment variables)
export FEATURE_MCP_COLLECTOR=true
if feature_flags.feature("MCP_COLLECTOR"):
    pass
```

## Known Limitations

1. **Case-sensitive env var names**: Must use uppercase with underscores
2. **LRU cache**: Uses `lru_cache` which may need clearing in some scenarios
3. **Thread safety**: Cache clearing is not thread-safe (use with care)
4. **No persistence**: Feature flags reset on application restart

## See Also

- [HEADLESS-MODE.md](./HEADLESS-MODE.md) - Headless mode documentation
- [ARCHITECTURE-COMPARISON-REVIEW-V3.md](../ARCHITECTURE-COMPARISON-REVIEW-V3.md) - Architecture review
- [backend/main.py](../main.py) - Main application entry point
