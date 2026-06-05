"""editing 子系统自带单测：纯逻辑，无需 DB / 执行器。"""
import os
import sys
import unittest

# 让 `import editing` 在从 backend/ 跑 pytest 时可用
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from editing import apply_replacement, EditError, build_completion_hint


class TestApplyReplacement(unittest.TestCase):
    def test_exact_single_replace(self):
        content = "line a\nline b\nline c\n"
        out = apply_replacement(content, "line b", "LINE B")
        self.assertEqual(out, "line a\nLINE B\nline c\n")

    def test_not_found_raises(self):
        with self.assertRaises(EditError):
            apply_replacement("hello world", "missing", "x")

    def test_empty_old_string_raises(self):
        with self.assertRaises(EditError):
            apply_replacement("abc", "", "x")

    def test_identical_old_new_raises(self):
        with self.assertRaises(EditError):
            apply_replacement("abc", "ab", "ab")

    def test_non_unique_without_replace_all_raises(self):
        with self.assertRaises(EditError):
            apply_replacement("foo\nfoo\n", "foo", "bar")

    def test_replace_all(self):
        out = apply_replacement("foo\nfoo\n", "foo", "bar", replace_all=True)
        self.assertEqual(out, "bar\nbar\n")

    def test_unique_with_context(self):
        content = "x = 1\ny = 1\n"
        out = apply_replacement(content, "x = 1", "x = 2")
        self.assertEqual(out, "x = 2\ny = 1\n")

    def test_line_trimmed_fallback_matches_trailing_whitespace(self):
        # 文件里该行有行尾空格，模型给的 old_string 没有 → 回退匹配仍命中
        content = "def f():   \n    return 1\n"
        out = apply_replacement(content, "def f():", "def g():")
        self.assertEqual(out, "def g():   \n    return 1\n")

    def test_whitespace_normalized_fallback(self):
        # 内部空白数量不同 → 归一化后命中
        content = "a   =    1\n"
        out = apply_replacement(content, "a = 1", "a = 2")
        self.assertEqual(out, "a = 2\n")


class TestCompletionHint(unittest.TestCase):
    def test_hint_mentions_edit_file_not_replace_file_content(self):
        hint = build_completion_hint("write_file", "calc/index.html", 1234)
        self.assertIn("edit_file", hint)
        self.assertNotIn("replace_file_content", hint)
        self.assertIn("1234", hint)
        self.assertIn("calc/index.html", hint)


if __name__ == "__main__":
    unittest.main()
