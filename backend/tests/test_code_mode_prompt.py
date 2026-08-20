import unittest

from executors.code_mode import CODE_MODE_PROMPT, append_code_mode_prompt


class CodeModePromptTest(unittest.TestCase):
    def test_prompt_is_injected_only_for_available_run_code(self) -> None:
        base = "base"
        schema = [{"function": {"name": "run_code"}}]
        self.assertIn(CODE_MODE_PROMPT, append_code_mode_prompt(base, schema))
        self.assertEqual(append_code_mode_prompt(base, []), base)

    def test_malformed_schema_does_not_enable_code_mode(self) -> None:
        self.assertEqual(append_code_mode_prompt("base", [{"name": "run_code"}]), "base")


if __name__ == "__main__":
    unittest.main()
