"""Temporary upload storage with TTL cleanup."""

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path


class UploadTooLarge(Exception):
    pass


class UnsupportedFileType(Exception):
    pass


# 允许上传的音频扩展名白名单（与 web/js/app.js 中的列表保持一致）。
SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
     ".wma", ".amr", ".aif", ".aiff", ".webm"}
)


class UploadStore:
    def __init__(
        self,
        root: str | Path | None = None,
        ttl_seconds: int = 2 * 3600,
        max_items: int = 200,
    ):
        self.root = Path(root or Path(tempfile.gettempdir()) / "sherpa_asr_uploads")
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._lock = threading.Lock()

    def _dir(self, upload_id: str) -> Path:
        return self.root / upload_id

    def save(self, upload, max_bytes: int, filename: str) -> dict:
        upload_id = uuid.uuid4().hex
        ext = os.path.splitext(filename)[1][:10].lower()
        if ext not in SUPPORTED_AUDIO_EXTENSIONS:
            raise UnsupportedFileType(filename)

        dst_dir = self._dir(upload_id)
        dst_dir.mkdir(parents=True, exist_ok=False)

        raw_path = dst_dir / f"source{ext}"
        size = 0
        try:
            with open(raw_path, "wb") as out:
                while True:
                    chunk = upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge(max_bytes)
                    out.write(chunk)
        except Exception:
            self.delete(upload_id)
            raise

        meta = {
            "id": upload_id,
            "filename": filename,
            "path": str(raw_path),
            "size": size,
            "created": time.time(),
            "duration": None,
        }
        (dst_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        self.prune()
        return meta

    def get(self, upload_id: str) -> dict | None:
        meta_path = self._dir(upload_id) / "meta.json"
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if not Path(meta.get("path", "")).is_file():
            return None
        return meta

    def update_duration(self, upload_id: str, duration: float | None):
        meta_path = self._dir(upload_id) / "meta.json"
        if not meta_path.is_file():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        meta["duration"] = duration
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def delete(self, upload_id: str):
        """Delete one upload dir. Caller must hold self._lock when concurrency matters."""
        dst = self._dir(upload_id)
        if dst.is_dir():
            for p in dst.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                dst.rmdir()
            except OSError:
                pass

    def prune(self):
        now = time.time()
        with self._lock:
            items = sorted(
                (
                    (p, (p / "meta.json").stat().st_mtime if (p / "meta.json").is_file() else 0)
                    for p in self.root.iterdir()
                    if p.is_dir()
                ),
                key=lambda x: x[1],
            )
            for path, mtime in items:
                if now - mtime > self.ttl_seconds or len(items) > self.max_items:
                    self.delete(path.name)
                    items = [x for x in items if x[0] != path]
