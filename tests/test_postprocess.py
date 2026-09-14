"""Unit tests for text postprocessing."""

import unittest
from app.postprocess import (
    ascii_punctuation_for_english,
    looks_all_caps,
    normalize_english_case,
    text_stats,
)


class TestPostprocess(unittest.TestCase):
    def test_normalize_english_with_dot(self):
        text = "HELLO WORLD. THIS IS A TEST. WE LOVE CODING."
        result = normalize_english_case(text)
        self.assertEqual(result, "Hello world. This is a test. We love coding.")

    def test_standalone_i(self):
        text = "YESTERDAY I WENT TO SCHOOL AND I SAW HIM."
        result = normalize_english_case(text)
        self.assertEqual(result, "Yesterday I went to school and I saw him.")

    def test_looks_all_caps_pure_english(self):
        self.assertTrue(looks_all_caps("HELLO WORLD HOW ARE YOU"))
        self.assertFalse(looks_all_caps("Hello world how are you"))
        self.assertFalse(looks_all_caps("hello world"))

    def test_looks_all_caps_cjk_with_acronyms(self):
        # Chinese sentence with English acronyms should NOT be treated as all-caps English
        self.assertFalse(looks_all_caps("我今天买了 APPLE 电脑，CPU 很强。"))
        self.assertFalse(looks_all_caps("这个 GPT 模型的 API 很方便。"))

    def test_ascii_punctuation_spacing(self):
        text = "Hello world，this is nice。Next sentence！Really？Yes；done：ok"
        result = ascii_punctuation_for_english(text)
        self.assertEqual(result, "Hello world, this is nice. Next sentence! Really? Yes; done: ok")

    def test_ascii_punctuation_does_not_break_numbers(self):
        text = "Value is 3.14159."
        result = ascii_punctuation_for_english(text)
        self.assertEqual(result, "Value is 3.14159.")

    def test_text_stats(self):
        stats = text_stats("今天天气非常好，Hello World！")
        # 7 CJK chars ('今天天气非常好') + 2 Latin words ('Hello', 'World') = 9
        self.assertEqual(stats["char_count"], 9)


if __name__ == "__main__":
    unittest.main()
