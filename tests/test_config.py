"""Unit tests for config loading."""

import unittest
from app.config import load_models


class TestConfig(unittest.TestCase):
    def test_load_models_structure(self):
        default_id, specs, punct_specs, default_punct, vad = load_models()
        self.assertIsInstance(default_id, str)
        self.assertIsInstance(specs, list)
        self.assertGreater(len(specs), 0)
        self.assertIsInstance(punct_specs, list)
        self.assertTrue(default_punct is None or isinstance(default_punct, str))


if __name__ == "__main__":
    unittest.main()
