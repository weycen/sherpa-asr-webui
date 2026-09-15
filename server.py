"""FastAPI 入口：上传接口 + 长音频分段转写 + 托管 web/ 前端。"""

import asyncio
import logging
import os
import sys
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import DEFAULT_MODELS_FILE, ROOT_DIR, load_models
from app.downloader import YtDlpManager
from app.model_manager import ModelLoadError, ModelManager, ModelNotFoundError
from app.pipeline import transcribe_audio_file
from app.postprocess import text_stats
from app.punctuator import PunctuationManager, PunctuationNotFoundError
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
MAX_STORAGE_BYTES = int(_env_float("ASR_MAX_STORAGE_GB", 5.0) * 1024 * 1024 * 1024)
MAX_AUDIO_SECONDS = _env_float("ASR_MAX_AUDIO_SECONDS", 0) or None
MAX_CONCURRENT = max(1, int(os.getenv("ASR_MAX_CONCURRENT", "2")))
MODELS_FILE = os.getenv("ASR_MODELS_FILE") or str(DEFAULT_MODELS_FILE)
UPLOAD_ROOT = os.getenv("ASR_UPLOAD_DIR") or str(
    Path(tempfile.gettempdir()) / "sherpa_asr_uploads"
)

# 配置文件缺失/非法属于启动期错误；模型本体按需懒加载，缺文件只影响对应模型。
try:
    DEFAULT_MODEL_ID, MODEL_SPECS, PUNCT_SPECS, DEFAULT_PUNCT_ID, VAD_SPEC = load_models(MODELS_FILE)
except Exception as exc:
    print(f"[错误] 加载模型配置失败: {exc}", file=sys.stderr)
    sys.exit(1)

manager = ModelManager(MODEL_SPECS, DEFAULT_MODEL_ID)
punctuation_manager = PunctuationManager(PUNCT_SPECS, DEFAULT_PUNCT_ID) if PUNCT_SPECS else None
segmenter = Segmenter(VAD_SPEC)
upload_store = UploadStore(UPLOAD_ROOT, max_total_bytes=MAX_STORAGE_BYTES)
concurrency_gate = threading.BoundedSemaphore(MAX_CONCURRENT)

YTDLP_COOKIES_FILE = os.getenv("ASR_YTDLP_COOKIES_FILE")
YTDLP_PROXY = os.getenv("ASR_YTDLP_PROXY")
YTDLP_EXTRACTOR_ARGS = os.getenv("ASR_YTDLP_EXTRACTOR_ARGS")
ytdlp_manager = YtDlpManager(
    upload_store=upload_store,
    cookies_file=YTDLP_COOKIES_FILE,
    proxy=YTDLP_PROXY,
    max_audio_seconds=MAX_AUDIO_SECONDS,
    extractor_args=YTDLP_EXTRACTOR_ARGS,
)

# 进行中的转写任务：upload_id -> {"cancel_event", "done", "total"}。
_job_lock = threading.Lock()
_jobs: dict[str, dict] = {}


async def _periodic_prune(interval_seconds: int = 900):
    """后台每隔 interval_seconds (默认15分钟) 执行一次过期文件与超额配额清理。"""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            upload_store.prune()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[警告] 定期清理上传文件失败: {exc}", file=sys.stderr)


class NoPollingLogFilter(logging.Filter):
    """过滤高频轮询进度与音频分片请求的访问日志，避免终端刷屏，仅保留启动、转写、错误等关键日志。"""

    EXCLUDED_PREFIXES = ("/api/ytdlp/progress", "/api/transcribe/progress", "/api/audio/")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and len(record.args) >= 3:
            path = str(record.args[2])
            status_code = str(record.args[4]) if len(record.args) >= 5 else "200"
            if status_code in ("200", "206", "304"):
                if any(path.startswith(prefix) for prefix in self.EXCLUDED_PREFIXES):
                    return False
        else:
            msg = record.getMessage()
            if any(prefix in msg for prefix in self.EXCLUDED_PREFIXES):
                if any(code in msg for code in (" 200 ", " 206 ", " 304 ")):
                    return False
        return True


logging.getLogger("uvicorn.access").addFilter(NoPollingLogFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保在 lifespan 中也安装日志过滤器（适应多 worker 与 reload 模式）
    logging.getLogger("uvicorn.access").addFilter(NoPollingLogFilter())

    # 启动阶段：清理上次异常退出的中间 WAV 文件并执行初始淘汰
    swept = upload_store.cleanup_orphan_wavs()
    if swept:
        print(f"已清理上次异常退出残留的中间文件: {swept} 个")
    upload_store.prune()

    cleanup_task = asyncio.create_task(_periodic_prune(interval_seconds=900))
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        for task_id in list(ytdlp_manager._jobs.keys()):
            try:
                await ytdlp_manager.cancel_job(task_id)
            except Exception:
                pass


app = FastAPI(title="Sherpa ASR WebUI", lifespan=lifespan)


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


def _resolve_punctuator(use_punctuation: bool, punctuation_model: str):
    """Pick the requested punctuation model, "" = default, "none" = disabled."""
    if not use_punctuation or punctuation_model == "none" or punctuation_manager is None:
        return None
    punct_id = punctuation_model or punctuation_manager.default_id
    if not punct_id:
        return None
    try:
        return punctuation_manager.get(punct_id)
    except PunctuationNotFoundError:
        raise HTTPException(status_code=404, detail=f"未知标点模型: {punct_id}")


@app.get("/api/models")
def list_models():
    return {
        "default_model": manager.default_model_id,
        "models": manager.list(),
        "default_punctuation": punctuation_manager.default_id if punctuation_manager else None,
        "punctuations": punctuation_manager.list() if punctuation_manager else [],
        "ytdlp_available": ytdlp_manager.is_available(),
    }


@app.post("/api/upload")
def upload_audio(request: Request, file: UploadFile = File(...)):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                )
        except ValueError:
            pass

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


@app.get("/api/audio/{upload_id}")
def get_audio(upload_id: str):
    meta = upload_store.get(upload_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="音频不存在或已过期")
    path = meta.get("path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="音频源文件不存在")
    upload_store.touch(upload_id)
    return FileResponse(
        path=path,
        filename=meta.get("filename", "audio"),
        media_type="audio/*",
    )


@app.post("/api/transcribe")
def transcribe_audio(
    upload_id: str = Form(...),
    model: str = Form(""),
    use_punctuation: bool = Form(True),
    punctuation_model: str = Form(""),
):
    meta = upload_store.get(upload_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="上传不存在或已过期，请重新上传")

    upload_store.touch(upload_id)

    # 转码前先行校验时长限制，避免超长文件无效转码浪费资源
    if MAX_AUDIO_SECONDS and meta.get("duration"):
        if meta["duration"] > MAX_AUDIO_SECONDS:
            raise HTTPException(
                status_code=413,
                detail=f"音频时长 {meta['duration']:.0f} 秒，超过限制 {int(MAX_AUDIO_SECONDS)} 秒",
            )

    model_id = model or manager.default_model_id
    punct = _resolve_punctuator(use_punctuation, punctuation_model)
    source_path = meta["path"]
    # 每次请求单独一份中间 WAV，避免同一 upload 并发转写互相覆盖 / 删除。
    wav_path = os.path.join(
        os.path.dirname(source_path), f"converted.{uuid.uuid4().hex}.16k.wav"
    )

    job = {"cancel_event": threading.Event(), "stage": "queued", "done": 0, "total": 0}
    _set_job(upload_id, job)
    upload_store.activate(upload_id)

    def set_stage(stage: str):
        with _job_lock:
            current = _jobs.get(upload_id)
            if current is job:
                current["stage"] = stage

    def on_progress(done: int, total: int):
        with _job_lock:
            current = _jobs.get(upload_id)
            if current is job:
                current["stage"] = "transcribing"
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
            set_stage("converting")
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

            set_stage("segmenting")
            result = transcribe_audio_file(
                runtime,
                punct,
                segmenter,
                wav_path,
                use_punctuation=punct is not None,
                progress=on_progress,
                should_cancel=cancel_event.is_set,
            )
        stats = text_stats(result["text"])
        return {
            "status": "success",
            "model": runtime.spec.id,
            "text": result["text"],
            "paragraphs": result["paragraphs"],
            "segments": result.get("segment_items", []),
            "segment_count": result.get("segments", 0),
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
        upload_store.deactivate(upload_id)
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
        return {"active": False, "stage": "idle", "done": 0, "total": 0}
    return {
        "active": True,
        "stage": job.get("stage", "transcribing"),
        "done": job["done"],
        "total": job["total"],
    }


class YtDlpStartRequest(BaseModel):
    url: str


@app.post("/api/ytdlp/start")
async def ytdlp_start(req: YtDlpStartRequest):
    if not ytdlp_manager.is_available():
        raise HTTPException(status_code=503, detail="服务端未安装或找不到 yt-dlp 工具，无法下载在线视频")
    try:
        task_id = await ytdlp_manager.start_download(req.url)
        return {"status": "started", "task_id": task_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建下载任务失败: {exc}")


@app.get("/api/ytdlp/progress/{task_id}")
async def ytdlp_progress(task_id: str):
    job = await ytdlp_manager.get_job(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="下载任务不存在或已过期")
    return job


@app.post("/api/ytdlp/cancel/{task_id}")
async def ytdlp_cancel(task_id: str):
    cancelled = await ytdlp_manager.cancel_job(task_id)
    return {"status": "cancelled" if cancelled else "not_found"}


if not WEB_DIR.is_dir():
    print(f"[错误] 前端目录不存在: {WEB_DIR}", file=sys.stderr)
    sys.exit(1)

app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    print(f"模型配置文件: {MODELS_FILE}")
    print(f"默认模型: {manager.default_model_id}")
    print(f"上传大小上限: {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    print(f"存储空间配额: {MAX_STORAGE_BYTES // (1024 * 1024 * 1024)} GB")
    print(f"并发转写上限: {MAX_CONCURRENT}")
    print(f"yt-dlp 可用性: {'已就绪' if ytdlp_manager.is_available() else '未安装'}")
    uvicorn.run(app, host=HOST, port=PORT)
