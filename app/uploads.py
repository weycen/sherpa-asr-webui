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
        max_total_bytes: int = 5 * 1024 * 1024 * 1024,
    ):
        self.root = Path(root or Path(tempfile.gettempdir()) / "sherpa_asr_uploads")
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.max_total_bytes = max_total_bytes
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
        self.activate(upload_id)
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
        except Exception:
            self.delete(upload_id)
            raise
        finally:
            self.deactivate(upload_id)

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

    def touch(self, upload_id: str) -> None:
        """Update mtime of upload dir and meta.json to refresh TTL upon playback or transcription."""
        if not is_valid_upload_id(upload_id):
            return
        dst_dir = self._dir(upload_id)
        meta_path = dst_dir / "meta.json"
        now = time.time()
        try:
            os.utime(dst_dir, (now, now))
        except OSError:
            pass
        try:
            os.utime(meta_path, (now, now))
        except OSError:
            pass

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
                if not p.is_dir():
                    continue
                try:
                    dir_stat = p.stat()
                    meta_file = p / "meta.json"
                    if meta_file.is_file():
                        mtime = meta_file.stat().st_mtime
                    else:
                        # 尚无 meta.json：若目录是最近 30 分钟内创建的，判定为写入中或刚创建，豁免清理
                        if now - dir_stat.st_mtime < 1800:
                            continue
                        mtime = dir_stat.st_mtime

                    # 统计该目录占用的实际磁盘大小
                    dir_size = 0
                    for f in p.iterdir():
                        try:
                            if f.is_file():
                                dir_size += f.stat().st_size
                        except OSError:
                            pass
                except OSError:
                    continue

                item_entries.append((p, mtime, dir_size))

            # 按 mtime 由旧到新排序（最早访问的排在前面）
            item_entries.sort(key=lambda x: x[1])
            remaining = len(item_entries)
            total_bytes = sum(entry[2] for entry in item_entries)

            for path, mtime, size in item_entries:
                if path.name in active:
                    continue
                should_delete = (
                    (now - mtime > self.ttl_seconds)
                    or (remaining > self.max_items)
                    or (total_bytes > self.max_total_bytes)
                )
                if should_delete:
                    self.delete(path.name)
                    remaining -= 1
                    total_bytes -= size
                else:
                    self._clean_converted(path)
