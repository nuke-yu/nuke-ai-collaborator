import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.pricing import calculate_cost, _resolve_rates


class TestCalculateCost(unittest.TestCase):

    def test_deepseek_folds_cache_into_input(self):
        # deepseek input_tokens includes cache_read → full-rate input = 1000 - 200
        cost = calculate_cost("deepseek", "deepseek-chat", {
            "input_tokens": 1000, "output_tokens": 500,
            "cache_read_tokens": 200, "cache_creation_tokens": 0,
        })
        expected = (800 / 1e6 * 0.27) + (500 / 1e6 * 1.10) + (200 / 1e6 * 0.07)
        self.assertAlmostEqual(cost, expected, places=10)

    def test_claude_bills_cache_separately(self):
        # claude input_tokens excludes cache → no subtraction; cache_write counts
        cost = calculate_cost("claude", "claude-sonnet-4-5", {
            "input_tokens": 1000, "output_tokens": 500,
            "cache_read_tokens": 200, "cache_creation_tokens": 100,
        })
        expected = (1000 / 1e6 * 3.0) + (500 / 1e6 * 15.0) + \
                   (200 / 1e6 * 0.30) + (100 / 1e6 * 3.75)
        self.assertAlmostEqual(cost, expected, places=10)

    def test_openai_folds_cache_into_input(self):
        cost = calculate_cost("openai", "gpt-4o", {
            "input_tokens": 2000, "output_tokens": 1000,
            "cache_read_tokens": 500, "cache_creation_tokens": 0,
        })
        expected = (1500 / 1e6 * 2.50) + (1000 / 1e6 * 10.0) + (500 / 1e6 * 1.25)
        self.assertAlmostEqual(cost, expected, places=10)

    def test_ollama_is_free(self):
        cost = calculate_cost("ollama", "llama3", {
            "input_tokens": 9999, "output_tokens": 9999,
        })
        self.assertEqual(cost, 0.0)

    def test_unknown_provider_zero(self):
        self.assertEqual(calculate_cost("mystery", "x", {"input_tokens": 100}), 0.0)

    def test_none_and_missing_tokens_treated_as_zero(self):
        cost = calculate_cost("deepseek", "deepseek-chat", {
            "input_tokens": None, "output_tokens": None,
            "cache_read_tokens": None, "cache_creation_tokens": None,
        })
        self.assertEqual(cost, 0.0)

    def test_cache_read_exceeding_input_does_not_go_negative(self):
        cost = calculate_cost("deepseek", "deepseek-chat", {
            "input_tokens": 100, "output_tokens": 0,
            "cache_read_tokens": 500, "cache_creation_tokens": 0,
        })
        # full_input clamped at 0; only cache_read billed
        self.assertAlmostEqual(cost, 500 / 1e6 * 0.07, places=12)


class TestRateResolution(unittest.TestCase):

    def test_exact_match(self):
        self.assertIs(_resolve_rates("deepseek", "deepseek-reasoner"),
                      _resolve_rates("deepseek", "deepseek-reasoner"))
        self.assertEqual(_resolve_rates("deepseek", "deepseek-reasoner")["input"], 0.55)

    def test_longest_family_match_opus_45_beats_opus(self):
        r = _resolve_rates("claude", "claude-opus-4-5-20251101")
        self.assertEqual(r["input"], 5.0)   # opus-4-5 tier, not the $15 opus tier

    def test_opus_41_falls_back_to_opus(self):
        r = _resolve_rates("claude", "claude-opus-4-1")
        self.assertEqual(r["input"], 15.0)

    def test_sonnet_and_haiku_families(self):
        self.assertEqual(_resolve_rates("claude", "claude-3-7-sonnet")["input"], 3.0)
        self.assertEqual(_resolve_rates("claude", "claude-3-5-haiku")["input"], 1.0)

    def test_openai_family_match(self):
        self.assertEqual(_resolve_rates("openai", "gpt-4o-2024-08-06")["input"], 2.50)
        self.assertEqual(_resolve_rates("openai", "gpt-4o-mini")["input"], 0.15)

    def test_unknown_model_uses_provider_default(self):
        self.assertEqual(_resolve_rates("openai", "totally-new-model")["input"], 2.50)

    def test_unknown_provider_returns_none(self):
        self.assertIsNone(_resolve_rates("nope", "x"))


if __name__ == "__main__":
    unittest.main()
