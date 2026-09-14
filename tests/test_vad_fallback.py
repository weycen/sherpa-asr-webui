"""Test VAD fallback behavior when VadSpec is None or model missing."""

import unittest
import numpy as np

from app.vad import Segmenter
from app.config import VadSpec


class TestVadFallback(unittest.TestCase):
    def test_segmenter_with_none_spec(self):
        seg = Segmenter(None)
        self.assertFalse(seg.available())
        samples = np.zeros(16000 * 60, dtype=np.float32)  # 60 seconds
        chunks = seg.split(samples, 16000, mode="planner", max_seconds=25.0)
        self.assertTrue(len(chunks) > 0)
        # Should be split into fixed chunks of at most 25s
        for c in chunks:
            self.assertLessEqual(c.audio_end - c.audio_start, int(25.0 * 16000) + 1)

    def test_segmenter_with_nonexistent_model(self):
        spec = VadSpec(model="/path/to/nonexistent/model.onnx", max_speech_duration=20.0)
        seg = Segmenter(spec)
        self.assertFalse(seg.available())
        samples = np.zeros(16000 * 50, dtype=np.float32)
        chunks = seg.split(samples, 16000, mode="planner", max_seconds=20.0)
        self.assertTrue(len(chunks) > 0)


if __name__ == "__main__":
    unittest.main()
