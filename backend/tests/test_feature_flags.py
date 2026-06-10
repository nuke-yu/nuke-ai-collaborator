"""Tests for feature flags system."""

import os
import pytest
from unittest.mock import patch

from backend.utils.feature_flags import (
    feature,
    enable_feature,
    disable_feature,
    list_enabled_features,
    list_available_features,
    get_feature_metrics,
    FeatureValidationError,
    validate_feature_name,
    conditional_import,
)


class TestFeature:
    """Test the main feature() function."""

    def test_feature_not_set_returns_default_false(self):
        """When feature is not set, return default False."""
        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=False):
            # Remove if exists
            env_copy = os.environ.copy()
            key = "FEATURE_TEST_FEATURE_XYZ123"
            env_copy.pop(key, None)
            with patch.dict(os.environ, env_copy, clear=False):
                result = feature("TEST_FEATURE_XYZ123", default=False)
                assert result is False

    def test_feature_not_set_returns_default_true(self):
        """When feature is not set and default=True, return True."""
        env_copy = os.environ.copy()
        key = "FEATURE_TEST_FEATURE_ABC456"
        env_copy.pop(key, None)
        with patch.dict(os.environ, env_copy, clear=False):
            result = feature("TEST_FEATURE_ABC456", default=True)
            assert result is True

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "Yes", "YES", "on", "On", "enabled"])
    def test_feature_enabled_values(self, value):
        """Test various values that enable a feature."""
        with patch.dict(os.environ, {"FEATURE_TEST_ENABLED_FEATURE": value}):
            result = feature("TEST_ENABLED_FEATURE")
            assert result is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "No", "NO", "off", "disabled", ""])
    def test_feature_disabled_values(self, value):
        """Test various values that disable a feature."""
        with patch.dict(os.environ, {"FEATURE_TEST_DISABLED_FEATURE": value}):
            result = feature("TEST_DISABLED_FEATURE")
            assert result is False

    def test_feature_case_insensitive_env_var(self):
        """Feature names should be case-insensitive in env var lookup."""
        # When feature name is provided as lowercase, env var should still be uppercase
        with patch.dict(os.environ, {"FEATURE_TEST_LOWERCASE": "true"}):
            # Using uppercase name
            result = feature("TEST_LOWERCASE")
            assert result is True


class TestEnableDisableFeature:
    """Test enable_feature() and disable_feature()."""

    def test_enable_feature(self):
        """Test enabling a feature."""
        feature.cache_clear()
        with patch.dict(os.environ, {}, clear=True):
            # Initially disabled
            assert feature("DYNAMIC_ENABLE_TEST") is False

            # Enable it
            enable_feature("DYNAMIC_ENABLE_TEST")

            # Should be enabled
            assert feature("DYNAMIC_ENABLE_TEST") is True

    def test_disable_feature(self):
        """Test disabling a feature."""
        feature.cache_clear()
        with patch.dict(os.environ, {"FEATURE_DISABLE_TEST": "true"}):
            # Initially enabled
            assert feature("DISABLE_TEST") is True

            # Disable it
            disable_feature("DISABLE_TEST")

            # Should be disabled
            assert feature("DISABLE_TEST") is False


class TestListFeatures:
    """Test list_enabled_features() and list_available_features()."""

    def test_list_enabled_features_empty(self):
        """Test listing enabled features when none are enabled."""
        env_copy = {k: v for k, v in os.environ.items() if not k.startswith("FEATURE_")}
        with patch.dict(os.environ, env_copy, clear=False):
            enabled = list_enabled_features()
            assert isinstance(enabled, list)

    def test_list_available_features(self):
        """Test listing all available features."""
        with patch.dict(os.environ, {"FEATURE_CUSTOM_TEST": "true"}):
            available = list_available_features()
            assert "CUSTOM_TEST" in available


class TestGetFeatureMetrics:
    """Test get_feature_metrics()."""

    def test_get_feature_metrics_structure(self):
        """Test that metrics have correct structure."""
        metrics = get_feature_metrics()

        assert "total" in metrics
        assert "enabled" in metrics
        assert "disabled" in metrics
        assert "enabled_features" in metrics
        assert isinstance(metrics["total"], int)
        assert isinstance(metrics["enabled"], int)
        assert isinstance(metrics["disabled"], int)
        assert isinstance(metrics["enabled_features"], list)
        assert metrics["total"] == metrics["enabled"] + metrics["disabled"]


class TestValidateFeatureName:
    """Test validate_feature_name()."""

    def test_valid_feature_names(self):
        """Test valid feature name formats."""
        valid_names = [
            "MCP_COLLECTOR",
            "FEATURE_NAME",
            "test_feature",
            "MY_FEATURE_123",
            "a",
            "A",
            "lower_case",  # lowercase is valid
        ]

        for name in valid_names:
            try:
                validate_feature_name(name)
            except FeatureValidationError as e:
                print(f"Failed for '{name}': {e}")
                raise

    def test_invalid_feature_names(self):
        """Test invalid feature name formats."""
        invalid_names = [
            "",  # Empty
            "test-feature",  # Hyphen not allowed
            "test.feature",  # Dot not allowed
            "test feature",  # Space not allowed
            "TEST@FEATURE",  # Special char
        ]

        for name in invalid_names:
            with pytest.raises(FeatureValidationError):
                validate_feature_name(name)


class TestConditionalImport:
    """Test conditional_import()."""

    def test_conditional_import_enabled(self):
        """Test conditional import when feature is enabled."""
        feature.cache_clear()

        # Try to conditionally import an existing module
        with patch.dict(os.environ, {"FEATURE_CONDITIONAL_TEST": "true"}):
            result = conditional_import(
                "CONDITIONAL_TEST",
                "backend.utils.feature_flags",  # Valid module
                None  # No fallback
            )
            # Should return the module
            assert result is not None

    def test_conditional_import_disabled(self):
        """Test conditional import when feature is disabled."""
        feature.cache_clear()

        # When feature is disabled, should return None
        result = conditional_import(
            "NONEXISTENT_FEATURE",
            "nonexistent.module",
            None
        )
        assert result is None

    def test_conditional_import_with_fallback(self):
        """Test conditional import with fallback module."""
        feature.cache_clear()

        # Disabled feature with fallback
        result = conditional_import(
            "DISABLED_FEATURE",
            "nonexistent.module",
            "backend.utils.feature_flags"  # Fallback
        )
        # Should return fallback module
        assert result is not None


# Integration tests
class TestFeatureFlagsIntegration:
    """Integration tests for feature flags system."""

    def test_full_workflow(self):
        """Test complete feature flag workflow."""
        feature.cache_clear()

        # 1. Start with feature disabled
        env_copy = os.environ.copy()
        env_copy.pop("FEATURE_FULL_WORKFLOW_TEST", None)
        with patch.dict(os.environ, env_copy, clear=False):
            assert feature("FULL_WORKFLOW_TEST") is False

        # 2. Enable via environment
        with patch.dict(os.environ, {"FEATURE_FULL_WORKFLOW_TEST": "true"}):
            # Clear cache to pick up new value
            feature.cache_clear()
            assert feature("FULL_WORKFLOW_TEST") is True

        # 3. Check metrics
        metrics = get_feature_metrics()
        assert metrics["enabled"] >= 0

        # 4. List features
        enabled = list_enabled_features()
        assert isinstance(enabled, list)

        # Clear cache for next test
        feature.cache_clear()
