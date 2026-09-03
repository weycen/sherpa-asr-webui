"""FastAPI 入口：提供 /api 接口并托管 web/ 下的前端页面。"""

import os
import sys
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from app.config import DEFAULT_MODELS_FILE, ROOT_DIR, load_models
from app.model_manager import ModelLoadError, ModelManager, ModelNotFoundError
from app.transcriber import (
    TranscriptionError,
    audio_seconds,
    convert_to_16k_mono_wav,
    transcribe_wav,
)

WEB_DIR = ROOT_DIR / "web"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


HOST = os.getenv("ASR_HOST", "0.0.0.0")
PORT = int(os.getenv("ASR_PORT", "8000"))
MAX_UPLOAD_BYTES = int(_env_float("ASR_MAX_UPLOAD_MB", 512) * 1024 * 1024)
MAX_AUDIO_SECONDS = _env_float("ASR_MAX_AUDIO_SECONDS", 0) or None
MODELS_FILE = os.getenv("ASR_MODELS_FILE") or str(DEFAULT_MODELS_FILE)

# 配置文件缺失/非法属于启动期错误；模型本体按需懒加载，缺文件只影响对应模型。
try:
    DEFAULT_MODEL_ID, MODEL_SPECS = load_models(MODELS_FILE)
except Exception as exc:
    print(f"[错误] 加载模型配置失败: {exc}", file=sys.stderr)
    sys.exit(1)

manager = ModelManager(MODEL_SPECS, DEFAULT_MODEL_ID)

app = FastAPI(title="Sherpa ASR WebUI")


@app.get("/api/models")
def list_models():
    return {"default_model": manager.default_model_id, "models": manager.list()}


def _save_upload(upload: UploadFile) -> tuple[str, str]:
    """落盘上传文件并返回 (input_path, wav_path)，超过大小上限时抛出 413。"""
    suffix = os.path.splitext(upload.filename or "")[1][:10].lower()
    fd, input_path = tempfile.mkstemp(prefix="asr_upload_", suffix=suffix or ".audio")
    os.close(fd)

    size = 0
    with open(input_path, "wb") as dst:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                )
            dst.write(chunk)
    return input_path, input_path + ".16k.wav"


@app.post("/api/transcribe")
def transcribe_audio(model: str = Form(""), file: UploadFile = File(...)):
    model_id = model or manager.default_model_id
    try:
        runtime = manager.get(model_id)
    except ModelNotFoundError:
        raise HTTPException(status_code=404, detail=f"未知模型: {model_id}")
    except ModelLoadError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    input_path = wav_path = None
    try:
        input_path, wav_path = _save_upload(file)
        convert_to_16k_mono_wav(input_path, wav_path)

        if MAX_AUDIO_SECONDS:
            seconds = audio_seconds(wav_path)
            if seconds > MAX_AUDIO_SECONDS:
                raise HTTPException(
                    status_code=413,
                    detail=f"音频时长 {seconds:.0f} 秒，超过限制 {int(MAX_AUDIO_SECONDS)} 秒",
                )

        text, inf_time = transcribe_wav(runtime, wav_path)
        return {
            "status": "success",
            "model": runtime.spec.id,
            "text": text,
            "inference_time": inf_time,
        }
    except HTTPException:
        raise
    except TranscriptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"服务端处理失败: {exc}")
    finally:
        for path in (input_path, wav_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass


if not WEB_DIR.is_dir():
    print(f"[错误] 前端目录不存在: {WEB_DIR}", file=sys.stderr)
    sys.exit(1)

app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    print(f"模型配置文件: {MODELS_FILE}")
    print(f"默认模型: {manager.default_model_id}")
    print(f"上传大小上限: {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    uvicorn.run(app, host=HOST, port=PORT)
