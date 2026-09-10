"""ASR chunk planning, decoupled from voice-activity detection.

VAD only answers "where is speech". This module decides how to cut audio for
the recognizer: the default plan cuts at silence midpoints so every chunk keeps
the surrounding audio, and long chunks are split further with overlap so a cut
that has to fall inside continuous speech can be de-duplicated afterwards.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class Chunk:
    """One ASR input plus the metadata needed to merge chunk results."""

    audio_start: int  # sample index into the source audio
    audio_end: int
    speech_start: int  # real speech bounds inside this chunk
    speech_end: int
    boundary_before: str  # "audio_start" | "natural_silence" | "forced_overlap"
    boundary_after: str  # "natural_silence" | "forced_overlap" | "audio_end"
    overlap_after: int = 0  # samples shared with the next chunk


def midpoint_chunks(spans: list[tuple[int, int]], n_samples: int) -> list[Chunk]:
    """Cut contiguous audio at the midpoint of each silence between speech spans."""
    chunks = []
    for i, (a, b) in enumerate(spans):
        left = 0 if i == 0 else (spans[i - 1][1] + a) // 2
        right = n_samples if i == len(spans) - 1 else (b + spans[i + 1][0]) // 2
        before = "audio_start" if i == 0 else "natural_silence"
        after = "audio_end" if i == len(spans) - 1 else "natural_silence"
        chunks.append(Chunk(left, right, a, b, before, after))
    return chunks


def cap_duration(
    chunks: list[Chunk], n_samples: int, sr: int, max_seconds: float, overlap_seconds: float
) -> list[Chunk]:
    """Split chunks longer than max_seconds, overlapping so the cut can be merged."""
    if not max_seconds or max_seconds <= 0:
        return chunks
    max_samples = int(max_seconds * sr)
    overlap = int(max(0.0, overlap_seconds) * sr)
    if overlap >= max_samples:
        overlap = 0  # guarantee forward progress
    out = []
    for c in chunks:
        if c.audio_end - c.audio_start <= max_samples:
            out.append(c)
            continue
        start = c.audio_start
        before = c.boundary_before
        while start < c.audio_end:
            end = min(c.audio_end, start + max_samples)
            last = end >= c.audio_end
            speech_start = min(max(c.speech_start, start), end)
            speech_end = max(min(c.speech_end, end), speech_start)
            out.append(
                Chunk(
                    start,
                    end,
                    speech_start,
                    speech_end,
                    before,
                    c.boundary_after if last else "forced_overlap",
                    0 if last else overlap,
                )
            )
            if last:
                break
            start = end - overlap
            before = "forced_overlap"
    return out


def plan(
    spans: list[tuple[int, int]],
    n_samples: int,
    sr: int,
    max_seconds: float,
    overlap_seconds: float,
) -> list[Chunk]:
    """Full plan: midpoint cuts, then bound the chunk duration."""
    chunks = midpoint_chunks(spans, n_samples)
    return cap_duration(chunks, n_samples, sr, max_seconds, overlap_seconds)


def slice_audio(samples: np.ndarray, chunk: Chunk) -> np.ndarray:
    return np.ascontiguousarray(samples[chunk.audio_start : chunk.audio_end], dtype=np.float32)
