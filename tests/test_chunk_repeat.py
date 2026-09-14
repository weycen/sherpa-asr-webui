"""Unit tests for boundary repeat deduplication and segment joining."""

import unittest
from app.pipeline import _strip_boundary_repeat, _join_segments


class TestChunkRepeat(unittest.TestCase):
    def test_strip_cjk_repeat(self):
        prev = "这是一个很好的人工智能模型"
        cur = "人工智能模型正在快速发展"
        # "人工智能模型" (6 chars) should be stripped
        result = _strip_boundary_repeat(prev, cur)
        self.assertEqual(result, "正在快速发展")

    def test_strip_latin_repeat(self):
        prev = "we went to the market"
        cur = "to the market and bought fresh apples"
        result = _strip_boundary_repeat(prev, cur)
        self.assertEqual(result, "and bought fresh apples")

    def test_no_strip_when_no_overlap(self):
        prev = "hello world"
        cur = "completely different text"
        self.assertEqual(_strip_boundary_repeat(prev, cur), cur)

    def test_join_segments(self):
        self.assertEqual(_join_segments("Hello", "world"), "Hello world")
        self.assertEqual(_join_segments("Hello,", "world"), "Hello, world")
        self.assertEqual(_join_segments("你好", "世界"), "你好世界")
        self.assertEqual(_join_segments("你好，", "世界"), "你好，世界")


if __name__ == "__main__":
    unittest.main()
