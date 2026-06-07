"""Tests for secret redaction (#2 工具输出密钥脱敏)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.redaction import redact_secrets


class TestRedactSecrets(unittest.TestCase):

    def _assert_redacted(self, text, must_not_contain):
        out, n = redact_secrets(text)
        self.assertGreaterEqual(n, 1, f"expected a redaction in: {text!r}")
        self.assertIn("[REDACTED]", out)
        self.assertNotIn(must_not_contain, out)

    # --- high-confidence secret formats ---

    def test_github_token(self):
        self._assert_redacted("token=ghp_" + "a" * 40, "ghp_" + "a" * 40)

    def test_github_pat(self):
        self._assert_redacted("github_pat_" + "B" * 60, "B" * 60)

    def test_openai_key(self):
        self._assert_redacted("OPENAI: sk-" + "x" * 30, "sk-" + "x" * 30)

    def test_anthropic_key(self):
        secret = "sk-ant-" + "y" * 40
        self._assert_redacted(f"key {secret} here", secret)

    def test_aws_access_key_id(self):
        self._assert_redacted("AKIAIOSFODNN7EXAMPLE found", "AKIAIOSFODNN7EXAMPLE")

    def test_slack_token(self):
        self._assert_redacted("xoxb-123456789012-abcdefABCDEF", "xoxb-123456789012-abcdefABCDEF")

    def test_google_api_key(self):
        secret = "AIza" + "Z" * 35
        self._assert_redacted(secret, secret)

    def test_jwt(self):
        jwt = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 20
        self._assert_redacted(f"Bearer {jwt}", jwt)

    def test_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\nxyz\n-----END RSA PRIVATE KEY-----"
        out, n = redact_secrets(pem)
        self.assertEqual(n, 1)
        self.assertNotIn("MIIabc", out)

    def test_url_with_credentials_masks_password_only(self):
        out, n = redact_secrets("git remote: https://alice:s3cretpw@github.com/x.git")
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("s3cretpw", out)
        self.assertIn("alice", out)        # username preserved
        self.assertIn("github.com", out)

    def test_bearer_header(self):
        self._assert_redacted("Authorization: Bearer abcdef0123456789ABCDEF", "abcdef0123456789ABCDEF")

    def test_secret_named_assignment_keeps_key(self):
        out, n = redact_secrets("DB_PASSWORD=supersecret123")
        self.assertEqual(n, 1)
        self.assertIn("DB_PASSWORD=", out)   # key name preserved
        self.assertNotIn("supersecret123", out)

    def test_api_key_assignment(self):
        out, n = redact_secrets('export MY_API_KEY="abcd1234efgh"')
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("abcd1234efgh", out)

    # --- precision: must NOT redact these ---

    def test_pwd_not_redacted(self):
        out, n = redact_secrets("PWD=/home/user/project")
        self.assertEqual(n, 0)
        self.assertIn("/home/user/project", out)

    def test_api_url_not_redacted(self):
        out, n = redact_secrets("API_URL=https://api.example.com/v1")
        self.assertEqual(n, 0)

    def test_plain_text_not_redacted(self):
        out, n = redact_secrets("the build finished in 12.3s with 0 errors")
        self.assertEqual(n, 0)

    def test_git_sha_not_redacted(self):
        out, n = redact_secrets("commit a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0")
        self.assertEqual(n, 0)

    def test_short_text_noop(self):
        out, n = redact_secrets("ok")
        self.assertEqual((out, n), ("ok", 0))

    def test_count_multiple(self):
        text = f"a=ghp_{'a'*40} b=sk-{'x'*30}"
        _, n = redact_secrets(text)
        self.assertEqual(n, 2)


class TestRedactorAfterHook(unittest.IsolatedAsyncioTestCase):

    async def test_hook_redacts_and_reports(self):
        from executors.plugins.workspace_tools import _default_secret_redactor
        secret = "ghp_" + "z" * 40
        out = await _default_secret_redactor("run_shell", {}, f"leaked {secret}", {})
        self.assertIsNotNone(out)
        self.assertNotIn(secret, out)

    async def test_hook_noop_on_clean_output(self):
        from executors.plugins.workspace_tools import _default_secret_redactor
        out = await _default_secret_redactor("run_shell", {}, "all tests passed", {})
        self.assertIsNone(out)   # None = leave result unchanged

    async def test_registered_before_truncator(self):
        # The order guarantee: redactor must run before the truncator so the full
        # pre-truncation text is masked.
        from executors import tool_executor as te
        from executors.plugins import workspace_tools as wt
        before = list(te._after_hooks)
        try:
            te.clear_after_hooks()
            wt.register_workspace_tools()
            names = [e.fn.__name__ for e in te._after_hooks]
            self.assertIn("_default_secret_redactor", names)
            self.assertIn("_default_output_truncator", names)
            self.assertLess(
                names.index("_default_secret_redactor"),
                names.index("_default_output_truncator"),
            )
        finally:
            te._after_hooks[:] = before


if __name__ == "__main__":
    unittest.main()
