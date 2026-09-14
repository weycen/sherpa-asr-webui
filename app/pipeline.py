"""Long-audio transcription pipeline: VAD split -> chunked decode -> paragraphs."""

import os
import time

import numpy as np
import soundfile as sf

from .chunking import slice_audio
from .postprocess import (
    ascii_punctuation_for_english,
    looks_all_caps,
    normalize_english_case,
)
from .transcriber import TranscriptionCancelled

PARA_PAUSE_SECONDS = float(os.getenv("ASR_PARA_PAUSE_SECONDS", "1.6"))
PARA_MAX_SECONDS = float(os.getenv("ASR_PARA_MAX_SECONDS", "50"))
PARA_MAX_CHARS = int(os.getenv("ASR_PARA_MAX_CHARS", "400"))

# ASR chunking (see app/chunking.py). Strategy: legacy | midpoint | planner.
CHUNK_STRATEGY = os.getenv("ASR_CHUNK_STRATEGY", "planner")
CHUNK_MAX_SECONDS = float(os.getenv("ASR_CHUNK_MAX_SECONDS", "15"))
CHUNK_OVERLAP_SECONDS = float(os.getenv("ASR_CHUNK_OVERLAP_SECONDS", "2.0"))
# A repeated character is treated as a boundary artefact (a word split across
# two chunks) only when both copies come from the same moment in time.
BOUNDARY_DUP_SECONDS = float(os.getenv("ASR_BOUNDARY_DUP_SECONDS", "0.35"))


def _chunk_tokens(result, chunk, sample_rate: int, limit: float):
    """Return [(token, global_time)] for tokens newer than `limit`.

    Returns (None, text) when the model gives no usable timestamps so the
    caller can fall back to plain text handling.
    """
    tokens = list(getattr(result, "tokens", []) or [])
    stamps = list(getattr(result, "timestamps", []) or [])
    if not tokens or len(stamps) != len(tokens):
        return None, result.text.strip()
    base = chunk.audio_start / sample_rate
    kept = []
    last = limit
    for token, ts in zip(tokens, stamps):
        global_ts = base + ts
        if global_ts > last + 1e-3:
            kept.append((token, global_ts))
            last = global_ts
    return kept, ""


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3400 <= o <= 0x9FFF
        or 0xF900 <= o <= 0xFAFF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )


def _strip_boundary_repeat(prev: str, cur: str, max_chars: int = 12) -> str:
    """Drop a suffix/prefix repeat introduced by an overlapping cut.

    Only used when two chunks actually overlap in audio (forced_overlap),
    so natural (non-overlapping) boundaries are never altered.
    Supports both CJK characters and Latin words.
    """
    if not prev or not cur:
        return cur

    # 1. Latin word overlap check (e.g. "to the market" repeated)
    prev_words = prev.strip().split()
    cur_words = cur.strip().split()
    if prev_words and cur_words and (prev_words[-1].isascii() or cur_words[0].isascii()):
        max_w = min(len(prev_words), len(cur_words), 8)
        for k in range(max_w, 0, -1):
            if [w.lower() for w in prev_words[-k:]] == [w.lower() for w in cur_words[:k]]:
                matched_prefix = " ".join(cur_words[:k])
                idx = cur.lower().find(matched_prefix.lower())
                if idx >= 0:
                    return cur[idx + len(matched_prefix):].lstrip()

    # 2. CJK character overlap check
    limit = min(max_chars, len(prev), len(cur))
    for k in range(limit, 0, -1):
        seg = prev[-k:]
        if seg == cur[:k] and any(_is_cjk(c) for c in seg):
            return cur[k:]
    return cur


def _join_segments(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    if (a[-1].isascii() and (a[-1].isalnum() or a[-1] in ",.!?:;")) and b[0].isascii() and b[0].isalnum():
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
        current.append((start, duration, text))
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

    chunks = segmenter.split(
        samples,
        sample_rate,
        mode=CHUNK_STRATEGY,
        max_seconds=CHUNK_MAX_SECONDS,
        overlap_seconds=CHUNK_OVERLAP_SECONDS,
    )
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
    chunk_debug = []
    inference = 0.0
    last_gt = -1.0
    acc_text = ""
    last_kept = None  # (char, global_time) of the previous chunk's last token
    with runtime.lock:
        for i, chunk in enumerate(chunks):
            if should_cancel():
                raise TranscriptionCancelled()
            audio = slice_audio(samples, chunk)
            t0 = time.time()
            stream = runtime.recognizer.create_stream()
            stream.accept_waveform(sample_rate, audio)
            runtime.recognizer.decode_stream(stream)
            inference += time.time() - t0
            kept, raw_text = _chunk_tokens(stream.result, chunk, sample_rate, last_gt)
            if kept is None:
                if chunk.boundary_before == "forced_overlap":
                    text = _strip_boundary_repeat(acc_text, raw_text)
                else:
                    text = raw_text
            else:
                # Drop a leading token only if it repeats the previous chunk's
                # last token from the very same moment (a split word), not when
                # the speaker genuinely repeated it later.
                if (
                    kept
                    and last_kept
                    and kept[0][0] == last_kept[0]
                    and kept[0][1] - last_kept[1] <= BOUNDARY_DUP_SECONDS
                ):
                    kept = kept[1:]
                text = "".join(token for token, _ in kept)
                if kept:
                    last_kept = kept[-1]
                    last_gt = kept[-1][1]
            acc_text += text
            if text:
                duration = (chunk.speech_end - chunk.speech_start) / sample_rate
                entries.append((chunk.speech_start / sample_rate, duration, text))
            chunk_debug.append(
                {
                    "chunk_id": i,
                    "audio_start": round(chunk.audio_start / sample_rate, 3),
                    "audio_end": round(chunk.audio_end / sample_rate, 3),
                    "speech_start": round(chunk.speech_start / sample_rate, 3),
                    "speech_end": round(chunk.speech_end / sample_rate, 3),
                    "boundary_before": chunk.boundary_before,
                    "boundary_after": chunk.boundary_after,
                    "overlap_after": round(chunk.overlap_after / sample_rate, 3),
                    "text": text,
                }
            )
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
    out_segments = []
    for group in paragraphs:
        if should_cancel():
            raise TranscriptionCancelled()
        raw = ""
        for start, duration, text in group:
            raw = _join_segments(raw, text)
        all_caps = looks_all_caps(raw)
        if all_caps:
            raw = normalize_english_case(raw)
        if use_punctuation and punctuator is not None:
            raw = punctuator.add(raw)
            if all_caps:
                raw = normalize_english_case(ascii_punctuation_for_english(raw))
        clean = raw.strip()
        if clean:
            out_texts.append(clean)
            start_sec = round(group[0][0], 2)
            end_sec = round(group[-1][0] + group[-1][1], 2)
            out_segments.append(
                {
                    "start": start_sec,
                    "end": max(end_sec, round(start_sec + 0.1, 2)),
                    "text": clean,
                }
            )

    return {
        "text": "\n\n".join(t for t in out_texts if t),
        "paragraphs": len(out_texts),
        "inference_time": round(inference, 3),
        "segments": len(entries),
        "segment_items": out_segments,
        "audio_seconds": round(total_seconds, 1),
        "chunks": chunk_debug,
    }
