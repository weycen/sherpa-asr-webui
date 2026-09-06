"""Audio normalization and inference helpers."""

import json
import subprocess


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
    import soundfile as sf

    info = sf.info(path)
    return info.frames / info.samplerate


def ffprobe_duration(path: str) -> float | None:
    """Return media duration in seconds via ffprobe, or None when unavailable."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        return float(data["format"]["duration"])
    except Exception:
        return None
