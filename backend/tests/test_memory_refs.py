import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.application.references import (
    add_tool_ref_parameter,
    experience_ref,
    skill_ref,
    validate_tool_refs,
)


class TestMemoryRefs(unittest.TestCase):
    def test_stable_refs_are_derived_from_canonical_identity(self):
        self.assertEqual(experience_ref("exp:abc"), "exp:abc")
        self.assertEqual(skill_ref("skill:def", 3), "skill:def@v3")
        with self.assertRaisesRegex(ValueError, "experience"):
            experience_ref('memory_ref="exp:forged"')

    def test_tool_ref_allowlist_rejects_unknown_and_malformed_values(self):
        self.assertEqual(
            validate_tool_refs(["exp:abc", "exp:abc"], ("exp:abc",)),
            ("exp:abc",),
        )
        with self.assertRaisesRegex(ValueError, "not injected"):
            validate_tool_refs(["exp:other"], ("exp:abc",))
        with self.assertRaisesRegex(ValueError, "array"):
            validate_tool_refs("exp:abc", ("exp:abc",))

    def test_schema_augmentation_is_non_mutating_and_enumerates_refs(self):
        schemas = [{
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }]
        augmented = add_tool_ref_parameter(
            schemas, ("exp:abc", "skill:def@v3")
        )
        self.assertNotIn(
            "_memory_refs",
            schemas[0]["function"]["parameters"]["properties"],
        )
        ref_schema = augmented[0]["function"]["parameters"]["properties"][
            "_memory_refs"
        ]
        self.assertEqual(
            ref_schema["items"]["enum"], ["exp:abc", "skill:def@v3"]
        )


if __name__ == "__main__":
    unittest.main()
