"""Audio normalization and inference helpers."""

import subprocess
import time

import soundfile as sf


class TranscriptionError(Exception):
    """Raised when uploaded audio cannot be turned into text."""


def convert_to_16k_mono_wav(input_path: str, output_path: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        output_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace")[-400:]
        raise TranscriptionError(f"ffmpeg 转码失败: {detail}")


def audio_seconds(path: str) -> float:
    info = sf.info(path)
    return info.frames / info.samplerate


def transcribe_wav(runtime, wav_path: str) -> tuple[str, float]:
    """Run offline inference, returning (text, inference_seconds)."""
    audio, sample_rate = sf.read(wav_path, dtype="float32")

    t0 = time.time()
    with runtime.lock:  # sherpa-onnx recognizers are not safe for parallel decode
        stream = runtime.recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        runtime.recognizer.decode_stream(stream)
        text = stream.result.text.strip()
    inf_time = round(time.time() - t0, 3)

    return text, inf_time
