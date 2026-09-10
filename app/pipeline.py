"""Long-audio transcription pipeline: VAD split -> chunked decode -> paragraphs."""

import os
import time

import numpy as np
import soundfile as sf

from .postprocess import (
    ascii_punctuation_for_english,
    looks_all_caps,
    normalize_english_case,
)
from .transcriber import TranscriptionCancelled

PARA_PAUSE_SECONDS = float(os.getenv("ASR_PARA_PAUSE_SECONDS", "1.6"))
PARA_MAX_SECONDS = float(os.getenv("ASR_PARA_MAX_SECONDS", "50"))
PARA_MAX_CHARS = int(os.getenv("ASR_PARA_MAX_CHARS", "400"))


def _join_segments(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    if a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
        return f"{a} {b}"
    return a + b


def _group_paragraphs(entries, pause_break, max_seconds, max_chars):
    """Group speech chunks into reader-friendly paragraphs."""
    paragraphs = []
    current = []
    cur_seconds = 0.0
    cur_chars = 0
    prev_end = None
    for start, duration, text in entries:
        gap = (start - prev_end) if prev_end is not None else 0.0
        would_overflow = cur_seconds + duration > max_seconds or cur_chars + len(text) > max_chars
        if current and (gap >= pause_break or would_overflow):
            paragraphs.append(current)
            current = []
            cur_seconds = 0.0
            cur_chars = 0
        current.append(text)
        cur_seconds += duration
        cur_chars += len(text)
        prev_end = start + duration
    if current:
        paragraphs.append(current)
    return paragraphs


def transcribe_audio_file(
    runtime,
    punctuator,
    segmenter,
    wav_path: str,
    use_punctuation: bool,
    progress=None,
    should_cancel=None,
) -> dict:
    """Run the full pipeline and return result metadata + paragraph text.

    progress(done, total) is called after every decoded speech chunk.
    should_cancel() returning True aborts between chunks with
    TranscriptionCancelled, so a client can stop a long transcription.
    """
    if progress is None:
        progress = lambda done, total: None
    if should_cancel is None:
        should_cancel = lambda: False

    samples, sample_rate = sf.read(wav_path, dtype="float32")
    samples = np.ascontiguousarray(samples, dtype=np.float32)

    total_seconds = len(samples) / sample_rate

    chunks = segmenter.split(samples, sample_rate)
    if should_cancel():
        raise TranscriptionCancelled()
    if not chunks:
        return {
            "text": "",
            "paragraphs": 0,
            "inference_time": 0.0,
            "segments": 0,
            "audio_seconds": total_seconds,
        }

    entries = []  # (start_seconds, duration, raw_text)
    inference = 0.0
    with runtime.lock:
        for i, (start, chunk) in enumerate(chunks):
            if should_cancel():
                raise TranscriptionCancelled()
            chunk = np.ascontiguousarray(chunk, dtype=np.float32)
            t0 = time.time()
            stream = runtime.recognizer.create_stream()
            stream.accept_waveform(sample_rate, chunk)
            runtime.recognizer.decode_stream(stream)
            inference += time.time() - t0
            text = stream.result.text.strip()
            if text:
                entries.append((start, len(chunk) / sample_rate, text))
            progress(i + 1, len(chunks))

    if should_cancel():
        raise TranscriptionCancelled()

    paragraphs = _group_paragraphs(
        entries,
        pause_break=PARA_PAUSE_SECONDS,
        max_seconds=PARA_MAX_SECONDS,
        max_chars=PARA_MAX_CHARS,
    )

    out_texts = []
    for group in paragraphs:
        raw = ""
        for text in group:
            raw = _join_segments(raw, text)
        all_caps = looks_all_caps(raw)
        if all_caps:
            raw = normalize_english_case(raw)
        if use_punctuation and punctuator is not None:
            raw = punctuator.add(raw)
            if all_caps:
                raw = normalize_english_case(ascii_punctuation_for_english(raw))
        out_texts.append(raw.strip())

    return {
        "text": "\n\n".join(t for t in out_texts if t),
        "paragraphs": len(out_texts),
        "inference_time": round(inference, 3),
        "segments": len(entries),
        "audio_seconds": round(total_seconds, 1),
    }
