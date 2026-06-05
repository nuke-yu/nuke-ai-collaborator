import unittest
from ai.model_limits import resolve_max_tokens, _resolve_ceiling


class TestResolveCeiling(unittest.TestCase):
    def test_exact_model(self):
        self.assertEqual(_resolve_ceiling("deepseek", "deepseek-chat"), 8192)

    def test_family_substring_longest_wins(self):
        # 真实模型名带后缀，仍按家族子串命中
        self.assertEqual(_resolve_ceiling("claude", "claude-sonnet-4-6"), 64000)
        self.assertEqual(_resolve_ceiling("openai", "gpt-4.1-2025"), 32768)

    def test_unknown_model_uses_provider_default(self):
        self.assertEqual(_resolve_ceiling("claude", "some-future-claude"), 8192)

    def test_unknown_provider_global_default(self):
        self.assertEqual(_resolve_ceiling("mistral", "whatever"), 8192)


class TestResolveMaxTokens(unittest.TestCase):
    def test_no_config_uses_model_ceiling(self):
        self.assertEqual(resolve_max_tokens("claude", "claude-sonnet-4-6", None), 64000)

    def test_zero_treated_as_unset(self):
        self.assertEqual(resolve_max_tokens("deepseek", "deepseek-chat", 0), 8192)

    def test_configured_below_ceiling_respected(self):
        self.assertEqual(resolve_max_tokens("deepseek", "deepseek-chat", 4096), 4096)

    def test_configured_above_ceiling_clamped(self):
        self.assertEqual(resolve_max_tokens("deepseek", "deepseek-chat", 999999), 8192)


if __name__ == "__main__":
    unittest.main()
