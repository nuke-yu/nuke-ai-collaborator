"""位置映射归一器单测：重点是改长度的归一仍能映射回原文真实字节。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from editing.normalize import (
    normalize, iter_spans, CHAR_TRANSFORMS, ESCAPE_TRANSFORMS,
)


class TestNormalize(unittest.TestCase):
    def test_identity_when_no_transform_hits(self):
        nz = normalize("abc", CHAR_TRANSFORMS)
        self.assertEqual(nz.text, "abc")
        self.assertEqual(nz.src, [0, 1, 2, 3])      # 末尾哨兵

    def test_length_preserving_char_maps_one_to_one(self):
        nz = normalize("“x”", CHAR_TRANSFORMS)       # 弯引号 → 直引号
        self.assertEqual(nz.text, '"x"')
        self.assertEqual(len(nz.src), len(nz.text) + 1)

    def test_length_changing_escape_maps_back_to_original(self):
        # `\n`（2 原文字符）→ 换行（1 归一字符），src 须指回原文起点
        nz = normalize("a\\nb", ESCAPE_TRANSFORMS)   # 原文是反斜杠+n
        self.assertEqual(nz.text, "a\nb")
        # 归一文 "a\nb"：索引 0=a(src0) 1=\n(src1) 2=b(src3)，末尾哨兵 src4
        self.assertEqual(nz.src, [0, 1, 3, 4])

    def test_iter_spans_returns_original_coords(self):
        content = "x = a\\nb = c"        # 原文含字面 \n
        spans = list(iter_spans(content, "a\nb", ESCAPE_TRANSFORMS))
        self.assertEqual(len(spans), 1)
        a, b = spans[0]
        self.assertEqual(content[a:b], "a\\nb")      # 切回的是原文真实字节（含反斜杠）


if __name__ == "__main__":
    unittest.main()
