"""Voice-activity detection and long-audio segmentation."""

import os
import sys

import numpy as np
import sherpa_onnx

from .config import VadSpec


class Segmenter:
    """Lazy-loaded silero VAD. Falls back to fixed windows when the model is absent."""

    def __init__(self, spec: VadSpec):
        self._spec = spec
        self._vad = None
        self._warned = False

    def available(self) -> bool:
        return os.path.isfile(self._spec.model)

    def _load(self):
        if not self.available():
            if not self._warned:
                print(
                    f"[警告] VAD 模型不存在: {self._spec.model}；改用固定长度分段。",
                    file=sys.stderr,
                )
                self._warned = True
            return None
        config = sherpa_onnx.VadModelConfig()
        config.sample_rate = self._spec.sample_rate
        config.silero_vad.model = self._spec.model
        config.silero_vad.threshold = self._spec.threshold
        config.silero_vad.min_speech_duration = self._spec.min_speech_duration
        config.silero_vad.min_silence_duration = self._spec.min_silence_duration
        config.silero_vad.max_speech_duration = self._spec.max_speech_duration
        self._vad = sherpa_onnx.VoiceActivityDetector(config)
        return self._vad

    def split(self, samples: np.ndarray, sample_rate: int) -> list[tuple[float, np.ndarray]]:
        """Return (start_seconds, float32 samples) speech chunks."""
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        total = len(samples) / sample_rate
        vad = self._load()
        if vad is None:
            # Fallback: fixed windows, keeps long files usable without the VAD model.
            window = max(int(self._spec.max_speech_duration * sample_rate), sample_rate)
            return [
                (i / sample_rate, samples[i : i + window])
                for i in range(0, len(samples), window)
            ]

        step = int(0.5 * sample_rate)
        for i in range(0, len(samples), step):
            vad.accept_waveform(samples[i : i + step])
        vad.flush()

        chunks = []
        while not vad.empty():
            segment = vad.front
            seg_samples = np.ascontiguousarray(
                np.asarray(segment.samples, dtype=np.float32)
            )
            if len(seg_samples) == 0:
                vad.pop()
                continue
            start = segment.start / sample_rate
            chunks.append((min(start, total), seg_samples))
            vad.pop()
        return chunks
