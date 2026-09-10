"""FastAPI 入口：上传接口 + 长音频分段转写 + 托管 web/ 前端。"""

import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.staticfiles import StaticFiles

from app.config import DEFAULT_MODELS_FILE, ROOT_DIR, load_models
from app.model_manager import ModelLoadError, ModelManager, ModelNotFoundError
from app.pipeline import transcribe_audio_file
from app.postprocess import text_stats
from app.punctuator import Punctuator
from app.transcriber import (
    TranscriptionCancelled,
    TranscriptionError,
    audio_seconds,
    convert_to_16k_mono_wav,
    ffprobe_duration,
)
from app.uploads import (
    SUPPORTED_AUDIO_EXTENSIONS,
    UploadStore,
    UploadTooLarge,
    UnsupportedFileType,
)
from app.vad import Segmenter

WEB_DIR = ROOT_DIR / "web"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


HOST = os.getenv("ASR_HOST", "0.0.0.0")
PORT = int(os.getenv("ASR_PORT", "8000"))
MAX_UPLOAD_BYTES = int(_env_float("ASR_MAX_UPLOAD_MB", 512) * 1024 * 1024)
MAX_AUDIO_SECONDS = _env_float("ASR_MAX_AUDIO_SECONDS", 0) or None
MAX_CONCURRENT = max(1, int(os.getenv("ASR_MAX_CONCURRENT", "2")))
MODELS_FILE = os.getenv("ASR_MODELS_FILE") or str(DEFAULT_MODELS_FILE)
UPLOAD_ROOT = os.getenv("ASR_UPLOAD_DIR") or str(
    Path(tempfile.gettempdir()) / "sherpa_asr_uploads"
)

# 配置文件缺失/非法属于启动期错误；模型本体按需懒加载，缺文件只影响对应模型。
try:
    DEFAULT_MODEL_ID, MODEL_SPECS, PUNCT_SPEC, VAD_SPEC = load_models(MODELS_FILE)
except Exception as exc:
    print(f"[错误] 加载模型配置失败: {exc}", file=sys.stderr)
    sys.exit(1)

manager = ModelManager(MODEL_SPECS, DEFAULT_MODEL_ID)
punctuator = Punctuator(PUNCT_SPEC) if PUNCT_SPEC else None
segmenter = Segmenter(VAD_SPEC) if VAD_SPEC else None
upload_store = UploadStore(UPLOAD_ROOT)
concurrency_gate = threading.BoundedSemaphore(MAX_CONCURRENT)

# 进行中的转写任务：upload_id -> {"cancel_event", "done", "total"}。
_job_lock = threading.Lock()
_jobs: dict[str, dict] = {}

app = FastAPI(title="Sherpa ASR WebUI")


def _get_job(upload_id: str) -> dict | None:
    with _job_lock:
        return _jobs.get(upload_id)


def _set_job(upload_id: str, job: dict):
    with _job_lock:
        _jobs[upload_id] = job


def _drop_job(upload_id: str, job: dict | None = None):
    with _job_lock:
        if job is None or _jobs.get(upload_id) is job:
            _jobs.pop(upload_id, None)


@app.get("/api/models")
def list_models():
    return {
        "default_model": manager.default_model_id,
        "punctuation_available": bool(punctuator and punctuator.available()),
        "models": manager.list(),
    }


@app.post("/api/upload")
def upload_audio(file: UploadFile = File(...)):
    filename = file.filename or "audio"
    try:
        meta = upload_store.save(file.file, MAX_UPLOAD_BYTES, filename)
    except UploadTooLarge:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    except UnsupportedFileType:
        supported = " / ".join(sorted(e.lstrip(".") for e in SUPPORTED_AUDIO_EXTENSIONS)).upper()
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型，仅支持 {supported} 音频",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存上传文件失败: {exc}")

    duration = ffprobe_duration(meta["path"])
    upload_store.update_duration(meta["id"], duration)
    return {
        "status": "success",
        "upload_id": meta["id"],
        "filename": meta["filename"],
        "size": meta["size"],
        "duration": duration,
    }


@app.post("/api/transcribe")
def transcribe_audio(
    upload_id: str = Form(...),
    model: str = Form(""),
    use_punctuation: bool = Form(True),
):
    meta = upload_store.get(upload_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="上传不存在或已过期，请重新上传")

    model_id = model or manager.default_model_id
    source_path = meta["path"]
    # 每次请求单独一份中间 WAV，避免同一 upload 并发转写互相覆盖 / 删除。
    wav_path = os.path.join(
        os.path.dirname(source_path), f"converted.{uuid.uuid4().hex}.16k.wav"
    )

    job = {"cancel_event": threading.Event(), "done": 0, "total": 0}
    _set_job(upload_id, job)

    def on_progress(done: int, total: int):
        with _job_lock:
            current = _jobs.get(upload_id)
            if current is job:
                current["done"] = done
                current["total"] = total

    cancel_event = job["cancel_event"]
    try:
        runtime = manager.get(model_id)
        if cancel_event.is_set():
            raise TranscriptionCancelled()
        with concurrency_gate:
            if cancel_event.is_set():
                raise TranscriptionCancelled()
            convert_to_16k_mono_wav(
                source_path, wav_path, should_cancel=cancel_event.is_set
            )
            if cancel_event.is_set():
                raise TranscriptionCancelled()
            if MAX_AUDIO_SECONDS:
                seconds = audio_seconds(wav_path)
                if seconds > MAX_AUDIO_SECONDS:
                    raise HTTPException(
                        status_code=413,
                        detail=f"音频时长 {seconds:.0f} 秒，超过限制 {int(MAX_AUDIO_SECONDS)} 秒",
                    )

            result = transcribe_audio_file(
                runtime,
                punctuator,
                segmenter,
                wav_path,
                use_punctuation=use_punctuation,
                progress=on_progress,
                should_cancel=cancel_event.is_set,
            )
        stats = text_stats(result["text"])
        return {
            "status": "success",
            "model": runtime.spec.id,
            "text": result["text"],
            "paragraphs": result["paragraphs"],
            "segments": result["segments"],
            "char_count": stats["char_count"],
            "inference_time": result["inference_time"],
            "audio_seconds": result["audio_seconds"],
        }
    except ModelNotFoundError:
        raise HTTPException(status_code=404, detail=f"未知模型: {model_id}")
    except ModelLoadError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except TranscriptionCancelled:
        return {"status": "cancelled", "model": model_id}
    except TranscriptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"服务端处理失败: {exc}")
    finally:
        _drop_job(upload_id, job)
        try:
            os.remove(wav_path)
        except OSError:
            pass


@app.post("/api/transcribe/cancel")
def cancel_transcribe(upload_id: str = Form(...)):
    job = _get_job(upload_id)
    if job is not None:
        job["cancel_event"].set()
    return {"status": "cancelled"}


@app.get("/api/transcribe/progress")
def transcribe_progress(upload_id: str = Query(...)):
    job = _get_job(upload_id)
    if job is None:
        return {"active": False, "done": 0, "total": 0}
    return {
        "active": True,
        "done": job["done"],
        "total": job["total"],
    }


if not WEB_DIR.is_dir():
    print(f"[错误] 前端目录不存在: {WEB_DIR}", file=sys.stderr)
    sys.exit(1)

app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    print(f"模型配置文件: {MODELS_FILE}")
    print(f"默认模型: {manager.default_model_id}")
    print(f"上传大小上限: {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    print(f"并发转写上限: {MAX_CONCURRENT}")
    uvicorn.run(app, host=HOST, port=PORT)
