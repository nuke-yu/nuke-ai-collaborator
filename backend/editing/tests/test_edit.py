"""editing 子系统自带单测：纯逻辑，无需 DB / 执行器。"""
import os
import sys
import unittest

# 让 `import editing` 在从 backend/ 跑 pytest 时可用
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from editing import (
    apply_replacement, EditError, build_completion_hint,
    strip_bom, detect_eol, to_lf, restore_eol,
)


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


class TestEquivalenceClassUniqueness(unittest.TestCase):
    """🔴 缺陷一：唯一性须按「等价类」判，而非精确字节 count。"""

    def test_two_trim_equivalent_blocks_are_not_unique(self):
        # 两块都在 line-trimmed 等价意义下匹配 find（缩进不同、字节不同），
        # 旧实现 count(第一块)==1 会静默改第一块；新实现须报「不唯一」。
        content = "if x:\n    return a\nif x:\n        return a\n"
        with self.assertRaises(EditError):
            apply_replacement(content, "if x:\nreturn a", "X")

    def test_replace_all_covers_equivalence_class(self):
        content = "if x:\n    return a\nif x:\n        return a\n"
        out = apply_replacement(content, "if x:\nreturn a", "if x:\n    return b",
                                replace_all=True)
        self.assertNotIn("return a", out)
        self.assertEqual(out.count("return b"), 2)


class TestWhitespaceNormalizedTrailingNewline(unittest.TestCase):
    r"""🟠 缺陷二：find 以 \n 结尾时第三阶段不应 miscount 窗口大小。"""

    def test_trailing_newline_still_matches_via_stage3(self):
        content = "a   =    1\nb = 2\n"
        out = apply_replacement(content, "a = 1\n", "a = 2")
        self.assertEqual(out, "a = 2\nb = 2\n")


class TestCharNormalization(unittest.TestCase):
    """P0 字符归一：弯引号 / unicode 空格 / 破折号（长度保持，拼回原字节）。"""

    def test_curly_quotes_match_straight(self):
        # 文件里是弯双引号，模型给直引号 → 命中，并保留替换文本
        content = "msg = “hello”\n"
        out = apply_replacement(content, 'msg = "hello"', 'msg = "bye"')
        self.assertEqual(out, 'msg = "bye"\n')

    def test_unicode_space_matches_ascii(self):
        # nbsp 两侧 =，模型给 ascii 空格 → 命中
        content = "a = 1\n"
        out = apply_replacement(content, "a = 1", "a = 2")
        self.assertEqual(out, "a = 2\n")

    def test_unicode_dash_matches_hyphen(self):
        # en-dash，模型给 ascii 连字符 → 命中
        content = "x = a – b\n"
        out = apply_replacement(content, "x = a - b", "x = a + b")
        self.assertEqual(out, "x = a + b\n")


class TestEolBoundary(unittest.TestCase):
    """P0 IO 边界：行尾/BOM 在 LF 平面匹配、写回还原。"""

    def test_strip_bom(self):
        self.assertEqual(strip_bom("﻿hi"), ("﻿", "hi"))
        self.assertEqual(strip_bom("hi"), ("", "hi"))

    def test_detect_eol(self):
        self.assertEqual(detect_eol("a\r\nb\r\n"), "\r\n")
        self.assertEqual(detect_eol("a\nb\n"), "\n")
        self.assertEqual(detect_eol("a\rb\r"), "\r")
        self.assertEqual(detect_eol("no newline"), "\n")

    def test_to_lf_and_restore_roundtrip(self):
        self.assertEqual(to_lf("a\r\nb\rc\n"), "a\nb\nc\n")
        self.assertEqual(restore_eol("a\nb\n", "\r\n"), "a\r\nb\r\n")
        self.assertEqual(restore_eol("a\nb\n", "\n"), "a\nb\n")

    def test_crlf_file_edited_with_lf_old_string_preserves_crlf(self):
        # 模拟 edit_file 的 IO 流程：CRLF 文件 + 模型给 LF 的 old_string
        raw = "﻿x = 1\r\ny = 2\r\n"
        bom, body = strip_bom(raw)
        eol = detect_eol(body)
        updated = apply_replacement(to_lf(body), to_lf("x = 1"), to_lf("x = 99"))
        out = bom + restore_eol(updated, eol)
        self.assertEqual(out, "﻿x = 99\r\ny = 2\r\n")   # BOM + CRLF 都保留


class TestBlockAnchor(unittest.TestCase):
    """L1：首尾行锚定、中间模糊（≥3 行）——接住长块内部被模型写歪的情况。"""

    def test_anchor_matches_with_wrong_interior(self):
        content = "def foo():\n    x = 1\n    y = 2\n    return x\n"
        # 模型记对了首尾行，中间写歪
        old = "def foo():\n    # whatever the model approximated\n    return x"
        new = "def foo():\n    return 42"
        out = apply_replacement(content, old, new)
        self.assertEqual(out, "def foo():\n    return 42\n")

    def test_anchor_ambiguous_is_not_unique(self):
        content = ("def f():\n    a = 1\n    return 1\n"
                   "def f():\n    b = 2\n    return 1\n")
        old = "def f():\n    ???\n    return 1"
        with self.assertRaises(EditError):
            apply_replacement(content, old, "def f():\n    pass")


class TestIndentReconciliation(unittest.TestCase):
    """L1：经忽略缩进的层命中后，new_string 须按命中块缩进重对齐，修 de-indent 坑。"""

    def test_line_trimmed_multiline_preserves_block_indent(self):
        # 模型漏了 4 空格体缩进；line_trimmed 命中，但替换须保留缩进，不能拍平
        content = "if cond:\n    a = 1\n    b = 2\n"
        old = "a = 1\nb = 2"
        new = "a = 10\nb = 20"
        out = apply_replacement(content, old, new)
        self.assertEqual(out, "if cond:\n    a = 10\n    b = 20\n")

    def test_exact_match_indent_not_reconciled(self):
        # simple 命中：缩进是模型有意给的，不动
        content = "    x = 1\n"
        out = apply_replacement(content, "    x = 1", "        x = 2")
        self.assertEqual(out, "        x = 2\n")


class TestCompletionHint(unittest.TestCase):
    def test_hint_mentions_edit_file_not_replace_file_content(self):
        hint = build_completion_hint("write_file", "calc/index.html", 1234)
        self.assertIn("edit_file", hint)
        self.assertNotIn("replace_file_content", hint)
        self.assertIn("1234", hint)
        self.assertIn("calc/index.html", hint)


if __name__ == "__main__":
    unittest.main()
