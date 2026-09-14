"""Voice-activity detection and long-audio segmentation."""

import os
import sys

import numpy as np
import sherpa_onnx

from .chunking import Chunk, midpoint_chunks, plan
from .config import VadSpec


class Segmenter:
    """Lazy-loaded silero VAD. Falls back to fixed windows when the model is absent."""

    def __init__(self, spec: VadSpec | None = None):
        self._spec = spec
        self._vad = None
        self._warned = False

    def available(self) -> bool:
        return bool(self._spec and self._spec.model and os.path.isfile(self._spec.model))

    def _load(self):
        if not self.available():
            if not self._warned and self._spec is not None:
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

    def detect(self, samples: np.ndarray, sample_rate: int) -> list[tuple[int, int]]:
        """Return (start_sample, end_sample) spans of detected speech only."""
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        vad = self._load()
        if vad is None:
            # Fallback: fixed windows, keeps long files usable without the VAD model.
            max_duration = self._spec.max_speech_duration if self._spec else 25.0
            window = max(int(max_duration * sample_rate), sample_rate)
            return [(i, min(i + window, len(samples))) for i in range(0, len(samples), window)]

        step = int(0.5 * sample_rate)
        for i in range(0, len(samples), step):
            vad.accept_waveform(samples[i : i + step])
        vad.flush()

        spans = []
        while not vad.empty():
            segment = vad.front
            start = int(segment.start)
            length = len(np.asarray(segment.samples))
            vad.pop()
            if length:
                spans.append((max(0, start), min(start + length, len(samples))))
        return spans

    def split(
        self,
        samples: np.ndarray,
        sample_rate: int,
        mode: str = "planner",
        max_seconds: float = 0.0,
        overlap_seconds: float = 0.0,
    ) -> list[Chunk]:
        """Turn speech spans into ASR chunks.

        mode="legacy"   cut at the VAD speech bounds (clips onset/offset audio)
        mode="midpoint" cut at silence midpoints, no duration bound
        mode="planner"  midpoint cuts plus a max-duration cap with overlap
        """
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        spans = self.detect(samples, sample_rate)
        if not spans:
            return []
        if mode == "legacy":
            return [
                Chunk(
                    a,
                    b,
                    a,
                    b,
                    "audio_start" if i == 0 else "natural_silence",
                    "audio_end" if i == len(spans) - 1 else "natural_silence",
                )
                for i, (a, b) in enumerate(spans)
            ]
        if mode == "midpoint":
            return midpoint_chunks(spans, len(samples))
        return plan(spans, len(samples), sample_rate, max_seconds, overlap_seconds)
