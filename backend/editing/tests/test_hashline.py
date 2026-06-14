"""行哈希锚点编辑单测：重点是位移后锚仍有效（drift-stable）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from editing.hashline import (
    line_hash, annotate, parse_anchor, apply_anchored_edits, HashlineError,
)


class TestAnnotate(unittest.TestCase):
    def test_annotate_tags_each_line(self):
        view = annotate("alpha\nbeta\n")
        self.assertIn("L1#", view)
        self.assertIn("L2#", view)
        self.assertIn("│ alpha", view)
        self.assertIn("│ beta", view)
        # 尾换行不产生幻影行
        self.assertEqual(len(view.split("\n")), 2)

    def test_parse_anchor_forms(self):
        self.assertEqual(parse_anchor("L12#a3f0c1d"), (12, "a3f0c1d"))
        self.assertEqual(parse_anchor("12#a3f0c1d"), (12, "a3f0c1d"))
        self.assertEqual(parse_anchor("#A3F0C1D"), (None, "a3f0c1d"))
        self.assertEqual(parse_anchor("garbage"), (None, None))


class TestApplyAnchored(unittest.TestCase):
    def _h(self, line):
        return line_hash(line)

    def test_replace_by_anchor(self):
        content = "a = 1\nb = 2\nc = 3\n"
        out = apply_anchored_edits(content, [{"anchor": f"#{self._h('b = 2')}", "text": "b = 99"}])
        self.assertEqual(out, "a = 1\nb = 99\nc = 3\n")

    def test_anchor_stays_valid_after_lines_shift(self):
        # 锚指向 'c = 3'；即便它从第 3 行漂到第 4 行，哈希不变 → 仍命中
        content = "INSERTED\na = 1\nb = 2\nc = 3\n"
        out = apply_anchored_edits(content, [{"anchor": f"L3#{self._h('c = 3')}", "text": "c = 30"}])
        self.assertEqual(out, "INSERTED\na = 1\nb = 2\nc = 30\n")

    def test_delete_and_insert_after(self):
        content = "x\ny\nz\n"
        out = apply_anchored_edits(content, [
            {"anchor": f"#{self._h('y')}", "op": "delete"},
            {"anchor": f"#{self._h('x')}", "op": "insert_after", "text": "x2"},
        ])
        self.assertEqual(out, "x\nx2\nz\n")

    def test_stale_anchor_raises(self):
        content = "a = 1\n"
        with self.assertRaises(HashlineError):
            apply_anchored_edits(content, [{"anchor": "#deadbee", "text": "x"}])

    def test_duplicate_line_needs_line_number(self):
        content = "dup\ndup\n"
        h = self._h("dup")
        with self.assertRaises(HashlineError):          # 无行号 → 歧义
            apply_anchored_edits(content, [{"anchor": f"#{h}", "text": "X"}])
        out = apply_anchored_edits(content, [{"anchor": f"L2#{h}", "text": "X"}])  # 带行号消歧
        self.assertEqual(out, "dup\nX\n")

    def test_conflicting_edits_on_same_line_raise(self):
        content = "a\nb\n"
        h = self._h("a")
        with self.assertRaises(HashlineError):
            apply_anchored_edits(content, [
                {"anchor": f"#{h}", "text": "A1"},
                {"anchor": f"L1#{h}", "text": "A2"},
            ])

    def test_empty_edits_raise(self):
        with self.assertRaises(HashlineError):
            apply_anchored_edits("x\n", [])


if __name__ == "__main__":
    unittest.main()
