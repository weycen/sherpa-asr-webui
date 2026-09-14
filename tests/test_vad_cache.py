"""Test VAD instance caching and reuse."""

import unittest
import soundfile as sf
from app.config import VadSpec
from app.vad import Segmenter


class TestVadCache(unittest.TestCase):
    def test_vad_caching_and_reset(self):
        spec = VadSpec(model="models/vad/silero_vad.onnx")
        seg = Segmenter(spec)
        self.assertTrue(seg.available())

        wav_path = "models/asr/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/test_wavs/en.wav"
        samples, sr = sf.read(wav_path, dtype="float32")

        # First call: initializes self._vad
        spans1 = seg.detect(samples, sr)
        vad_instance = seg._vad
        self.assertIsNotNone(vad_instance)

        # Second call: reuses the exact same self._vad instance
        spans2 = seg.detect(samples, sr)
        self.assertIs(seg._vad, vad_instance)
        self.assertEqual(spans1, spans2)


if __name__ == "__main__":
    unittest.main()
