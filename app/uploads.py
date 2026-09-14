"""Temporary upload storage with TTL cleanup."""

import collections
import json
import os
import re
import shutil
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

_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def is_valid_upload_id(upload_id: str) -> bool:
    """Upload ids are uuid4().hex; reject anything else before it hits the filesystem."""
    return bool(_UPLOAD_ID_RE.fullmatch(upload_id or ""))


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
        self._active: collections.Counter = collections.Counter()

    def activate(self, upload_id: str) -> None:
        """Mark an upload as busy so prune() never touches it mid-transcription."""
        with self._lock:
            self._active[upload_id] += 1

    def deactivate(self, upload_id: str) -> None:
        with self._lock:
            if self._active[upload_id] <= 1:
                self._active.pop(upload_id, None)
            else:
                self._active[upload_id] -= 1

    @staticmethod
    def _clean_converted(directory: Path) -> int:
        removed = 0
        for stale in directory.glob("converted.*"):
            try:
                stale.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def cleanup_orphan_wavs(self) -> int:
        """Delete leftover converted WAVs. Call once at startup, before serving."""
        removed = 0
        for entry in self.root.iterdir():
            if entry.is_dir():
                removed += self._clean_converted(entry)
        return removed

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
        if not is_valid_upload_id(upload_id):
            return None
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
        if not is_valid_upload_id(upload_id):
            return
        dst = self._dir(upload_id)
        if dst.is_dir():
            shutil.rmtree(dst, ignore_errors=True)

    def prune(self):
        now = time.time()
        with self._lock:
            active = {uid for uid, cnt in self._active.items() if cnt > 0}
            item_entries = []
            for p in self.root.iterdir():
                if p.is_dir():
                    try:
                        meta_file = p / "meta.json"
                        mtime = meta_file.stat().st_mtime if meta_file.is_file() else 0
                    except OSError:
                        mtime = 0
                    item_entries.append((p, mtime))

            item_entries.sort(key=lambda x: x[1])
            remaining = len(item_entries)
            for path, mtime in item_entries:
                if path.name in active:
                    continue
                if now - mtime > self.ttl_seconds or remaining > self.max_items:
                    self.delete(path.name)
                    remaining -= 1
                else:
                    self._clean_converted(path)
