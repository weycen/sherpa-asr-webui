"""Evaluate ASR chunking strategies on reproducible synthetic cases.

Builds long-form test audio by concatenating known utterances (optionally with
silence removed to force continuous speech), derives a reference transcript
from each utterance decoded in isolation, then reports CER / missing /
duplicated characters per strategy.

Run:
    /home/weycen/asr-service/venv/bin/python tests/eval.py

Note: the reference is model-derived (no human ground truth), so treat the
numbers as relative comparisons between strategies, not absolute accuracy.
"""

import difflib
import os
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import pipeline
from app.config import load_models
from app.model_manager import ModelManager
from app.punctuator import Punctuator
from app.vad import Segmenter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAVS = os.getenv(
    "ASR_TEST_WAVS",
    os.path.join(_ROOT, "models", "asr", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09", "test_wavs"),
)

# (case name, [(utterance, silence_after_seconds or None for glued/continuous)])
CASES = {
    "natural_pauses": [("zh.wav", 1.5), ("yue-2.wav", 1.5), ("yue-0.wav", 1.5), ("ko.wav", 1.5)],
    "short_pause": [("yue-0.wav", 0.3), ("yue-2.wav", 0.3), ("zh.wav", 0.3), ("yue-0.wav", 0.0)],
    "continuous": [("yue-1.wav", None), ("ja.wav", None), ("en.wav", None), ("zh.wav", None)],
}

STRATEGIES = [
    ("legacy", {"ASR_CHUNK_STRATEGY": "legacy"}),
    ("midpoint", {"ASR_CHUNK_STRATEGY": "midpoint"}),
    ("planner(15,2.0)", {"ASR_CHUNK_STRATEGY": "planner", "ASR_CHUNK_MAX_SECONDS": "15", "ASR_CHUNK_OVERLAP_SECONDS": "2.0"}),
    ("planner(25,2.5)", {"ASR_CHUNK_STRATEGY": "planner", "ASR_CHUNK_MAX_SECONDS": "25", "ASR_CHUNK_OVERLAP_SECONDS": "2.5"}),
]


def norm(text: str) -> str:
    return "".join(c.lower() for c in text if c.isalnum())


def edits(ref: str, hyp: str):
    sm = difflib.SequenceMatcher(None, ref, hyp)
    miss = dup = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            miss += i2 - i1
        if tag in ("insert", "replace"):
            dup += j2 - j1
    return miss, dup


def build_case(name, parts, workdir):
    path = os.path.join(workdir, f"{name}.wav")
    if all(s is not None for _, s in parts):
        silence = os.path.join(workdir, "sil.wav")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1.5", silence], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        listing = os.path.join(workdir, f"{name}.txt")
        with open(listing, "w") as fh:
            for utt, gap in parts:
                fh.write(f"file '{os.path.join(WAVS, utt)}'\n")
                if gap:
                    fh.write(f"file '{silence}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    else:
        listing = os.path.join(workdir, f"{name}.txt")
        with open(listing, "w") as fh:
            for utt, _ in parts:
                fh.write(f"file '{os.path.join(WAVS, utt)}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-af", "silenceremove=start_periods=1:start_threshold=-40dB:stop_periods=-1:stop_duration=0.12:stop_threshold=-40dB", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return path


def main():
    default, specs, punct_specs, default_punct, vad_spec = load_models()
    runtime = ModelManager(specs, default).get(default)
    punct_spec = next((s for s in punct_specs if s.id == default_punct), punct_specs[0] if punct_specs else None)
    punctuator = Punctuator(punct_spec) if punct_spec else None
    segmenter = Segmenter(vad_spec)

    def isolated(utt):
        samples, sr = sf.read(os.path.join(WAVS, utt), dtype="float32")
        res = pipeline.transcribe_audio_file(runtime, punctuator, segmenter, os.path.join(WAVS, utt), use_punctuation=False)
        return norm(res["text"])

    with tempfile.TemporaryDirectory() as workdir:
        for name, parts in CASES.items():
            path = build_case(name, parts, workdir)
            ref = "".join(isolated(utt) for utt, _ in parts)
            dur = sf.info(path).duration
            print(f"\n=== {name}  audio={dur:.1f}s  ref={len(ref)} chars ===")
            for label, env in STRATEGIES:
                pipeline.CHUNK_STRATEGY = env["ASR_CHUNK_STRATEGY"]
                pipeline.CHUNK_MAX_SECONDS = float(env.get("ASR_CHUNK_MAX_SECONDS", 25))
                pipeline.CHUNK_OVERLAP_SECONDS = float(env.get("ASR_CHUNK_OVERLAP_SECONDS", 2.5))
                res = pipeline.transcribe_audio_file(runtime, punctuator, segmenter, path, use_punctuation=False)
                hyp = norm(res["text"])
                miss, dup = edits(ref, hyp)
                cer = (miss + dup) / max(1, len(ref)) * 100
                forced = sum(1 for c in res["chunks"] if "forced" in c["boundary_after"])
                print(f"  {label:16s} chunks={len(res['chunks']):2d} forced={forced} len={len(hyp):3d} miss={miss:3d} dup={dup:3d} CER={cer:5.1f}%")


if __name__ == "__main__":
    main()
