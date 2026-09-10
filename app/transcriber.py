"""Audio normalization and inference helpers."""

import json
import subprocess
import tempfile
import time


class TranscriptionError(Exception):
    """Raised when uploaded audio cannot be turned into text."""


class TranscriptionCancelled(Exception):
    """Raised when the client aborts an in-flight transcription."""


def convert_to_16k_mono_wav(
    input_path: str, output_path: str, should_cancel=None
) -> None:
    """Transcode to 16k mono WAV; poll ffmpeg so a client cancel aborts a long run."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        output_path,
    ]
    # ffmpeg 会把进度写到 stderr，落到临时文件以免管道写满阻塞子进程。
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
        while proc.poll() is None:
            if should_cancel is not None and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise TranscriptionCancelled()
            time.sleep(0.1)
        if proc.returncode != 0:
            err.seek(0)
            detail = err.read().decode("utf-8", errors="replace")[-400:]
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
