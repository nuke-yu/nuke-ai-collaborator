import unittest

from sessions.manifest import build_capability_manifest


class TestSessionManifest(unittest.TestCase):
    def _build(self, **overrides):
        values = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "executor_id": "tool_loop_v1",
            "executor_version": "1",
            "system_prompt": "safe prompt",
            "bot": {"traits_json": "[]"},
            "tool_schemas": [{"name": "read_file", "parameters": {}}],
            "skills": [{"skill_id": "skill-1", "name": "review", "version": 2, "content": "secret"}],
            "permission_rules": [],
        }
        values.update(overrides)
        return build_capability_manifest(**values)

    def test_manifest_contains_hashes_but_not_prompt_or_skill_body(self):
        manifest, digest = self._build()
        serialized = str(manifest)
        self.assertEqual(manifest["manifest_version"], 1)
        self.assertEqual(digest, manifest_hash(manifest))
        self.assertIn("prompt_hash", manifest)
        self.assertNotIn("safe prompt", serialized)
        self.assertNotIn("secret", serialized)
        self.assertEqual(manifest["skills"][0]["skill_id"], "skill-1")

    def test_prompt_and_tool_changes_change_manifest_hash(self):
        _, first = self._build()
        _, second = self._build(system_prompt="changed")
        _, third = self._build(tool_schemas=[{"name": "write_file", "parameters": {}}])
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)


def manifest_hash(manifest):
    import hashlib
    import json
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
