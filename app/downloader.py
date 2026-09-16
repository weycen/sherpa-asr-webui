"""基于 yt-dlp 的在线音视频音频流提取与下载管理器。"""

import asyncio
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.uploads import UploadStore


_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_STD_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d+(?:\.\d+)?)%(?:.*?at\s+([^\s]+))?(?:.*?ETA\s+([^\s]+))?"
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mK]")
_INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|\x00-\x1f]')


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """去除文件名中的非法字符，避免写入文件系统或 HTTP Header 时出错。"""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "audio"
    return cleaned[:max_length]


def is_valid_web_url(url: str) -> bool:
    """验证是否为有效的 HTTP/HTTPS 链接。"""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    try:
        res = urlparse(url)
        return res.scheme in ("http", "https") and bool(res.netloc)
    except Exception:
        return False


@dataclass
class DownloadJob:
    task_id: str
    upload_id: str
    url: str
    status: str = "pending"  # pending | fetching | downloading | converting | ready | error | cancelled
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    title: str = ""
    duration: float | None = None
    filename: str = ""
    size: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    process: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "upload_id": self.upload_id if self.status == "ready" else None,
            "url": self.url,
            "status": self.status,
            "percent": round(self.percent, 1),
            "speed": self.speed,
            "eta": self.eta,
            "title": self.title,
            "duration": round(self.duration, 1) if self.duration is not None else None,
            "filename": self.filename,
            "size": self.size,
            "error": self.error,
        }


class YtDlpManager:
    def __init__(
        self,
        upload_store: UploadStore,
        executable: str | None = None,
        cookies_file: str | None = None,
        proxy: str | None = None,
        max_audio_seconds: float | None = None,
        extractor_args: str | None = None,
    ):
        self.upload_store = upload_store
        self.executable = executable or shutil.which("yt-dlp") or "yt-dlp"
        self.cookies_file = cookies_file
        self.proxy = proxy
        self.max_audio_seconds = max_audio_seconds
        self.extractor_args = extractor_args
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = asyncio.Lock()

    def is_available(self) -> bool:
        """检查系统中是否存在可执行的 yt-dlp。"""
        return bool(shutil.which(self.executable))

    async def start_download(self, url: str) -> str:
        """提交一个下载任务，返回 task_id。"""
        url = (url or "").strip()
        if not is_valid_web_url(url):
            raise ValueError("请输入有效的 http:// 或 https:// 视频链接")

        if not self.is_available():
            raise RuntimeError("服务端未安装或找不到 yt-dlp 工具，无法下载在线视频")

        task_id = uuid.uuid4().hex
        upload_id = uuid.uuid4().hex

        job = DownloadJob(task_id=task_id, upload_id=upload_id, url=url)
        async with self._lock:
            self._jobs[task_id] = job

        # 启动后台异步任务
        asyncio.create_task(self._run_job(job))
        return task_id

    async def get_job(self, task_id: str) -> dict | None:
        """获取指定任务的状态。"""
        async with self._lock:
            job = self._jobs.get(task_id)
            return job.to_dict() if job else None

    async def cancel_job(self, task_id: str) -> bool:
        """取消正在进行的下载任务。"""
        async with self._lock:
            job = self._jobs.get(task_id)
            if not job:
                return False
            if job.status in ("ready", "error", "cancelled"):
                return False
            job.cancel_requested = True
            job.status = "cancelled"
            proc = job.process

        if proc and proc.returncode is None:
            try:
                proc.terminate()
                # 给予进程 2 秒宽限期，若未退出则 SIGKILL
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
            except ProcessLookupError:
                pass
            except Exception:
                pass

        # 清理临时文件目录
        self.upload_store.delete(job.upload_id)
        return True

    async def _run_job(self, job: DownloadJob) -> None:
        """后台执行子进程并流式解析 yt-dlp 输出。"""
        self.upload_store.activate(job.upload_id)
        dst_dir = self.upload_store.root / job.upload_id
        dst_dir.mkdir(parents=True, exist_ok=True)

        dest_filepath: str | None = None
        stderr_chunks: list[str] = []

        try:
            job.status = "fetching"
            cmd = [
                self.executable,
                "--newline",
                "--no-playlist",
                "--progress",
                "--color",
                "never",
                "-x",
                "--audio-format",
                "mp3",
                "--progress-template",
                "download:PROGRESS:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
                "--print",
                "TITLE:%(title)s",
                "--print",
                "DURATION:%(duration)s",
                "--print",
                "after_move:DEST:%(filepath)s",
                "-o",
                str(dst_dir / "source.%(ext)s"),
            ]

            if self.extractor_args:
                cmd.extend(["--extractor-args", self.extractor_args])
            if self.cookies_file and os.path.isfile(self.cookies_file):
                cmd.extend(["--cookies", self.cookies_file])
            if self.proxy:
                cmd.extend(["--proxy", self.proxy])

            cmd.append(job.url)

            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            job.process = proc

            def _process_chunk(line: str) -> None:
                nonlocal dest_filepath
                line = _ANSI_ESCAPE.sub("", line).strip()
                if not line:
                    return

                if line.startswith("TITLE:"):
                    raw_title = line[6:].strip()
                    job.title = sanitize_filename(raw_title)

                elif line.startswith("DURATION:"):
                    try:
                        dur_val = float(line[9:].strip())
                        job.duration = dur_val
                        if self.max_audio_seconds and dur_val > self.max_audio_seconds:
                            job.status = "error"
                            job.error = f"视频音频时长（{int(dur_val)}秒）超过系统上限（{int(self.max_audio_seconds)}秒）"
                            if proc.returncode is None:
                                proc.terminate()
                    except (ValueError, TypeError):
                        pass

                elif line.startswith("PROGRESS:"):
                    job.status = "downloading"
                    parts = line[9:].split("|")
                    if parts:
                        pct_match = _PERCENT_RE.search(parts[0])
                        if pct_match:
                            try:
                                job.percent = float(pct_match.group(1))
                            except ValueError:
                                pass
                    if len(parts) >= 2:
                        s = parts[1].strip()
                        if s and s not in ("NA", "Unknown"):
                            job.speed = s
                    if len(parts) >= 3:
                        e = parts[2].strip()
                        if e and e not in ("NA", "Unknown"):
                            job.eta = e
                    if job.percent >= 100.0:
                        job.eta = "00:00"

                elif line.startswith("[download]"):
                    m = _STD_PROGRESS_RE.search(line)
                    if m:
                        job.status = "downloading"
                        pct_str, speed_str, eta_str = m.groups()
                        try:
                            job.percent = float(pct_str)
                        except (ValueError, TypeError):
                            pass
                        if speed_str and speed_str not in ("NA", "Unknown"):
                            job.speed = speed_str
                        if eta_str and eta_str not in ("NA", "Unknown"):
                            job.eta = eta_str
                        if job.percent >= 100.0:
                            job.eta = "00:00"

                elif line.startswith("DEST:"):
                    dest_filepath = line[5:].strip()

                elif "[ExtractAudio]" in line or "[ffmpeg]" in line:
                    job.status = "converting"
                    job.percent = 100.0
                    job.speed = ""
                    job.eta = ""

            def _handle_raw(raw_str: str) -> None:
                for chunk in raw_str.replace("\r\n", "\n").split("\r"):
                    for subline in chunk.split("\n"):
                        _process_chunk(subline)

            async def _read_stdout():
                while True:
                    line_bytes = await proc.stdout.readline()
                    if not line_bytes:
                        break
                    decoded = line_bytes.decode("utf-8", errors="replace")
                    _handle_raw(decoded)

            async def _read_stderr():
                while True:
                    line_bytes = await proc.stderr.readline()
                    if not line_bytes:
                        break
                    decoded = line_bytes.decode("utf-8", errors="replace")
                    stripped = decoded.strip()
                    if stripped:
                        stderr_chunks.append(stripped)
                        if len(stderr_chunks) > 50:
                            stderr_chunks.pop(0)
                    _handle_raw(decoded)

            await asyncio.gather(_read_stdout(), _read_stderr())
            await proc.wait()

            if job.cancel_requested or job.status == "cancelled":
                self.upload_store.delete(job.upload_id)
                return

            if job.status == "error":
                self.upload_store.delete(job.upload_id)
                return

            if proc.returncode != 0:
                job.status = "error"
                # 提炼 stderr 错误信息
                err_summary = ""
                for msg in reversed(stderr_chunks):
                    if "ERROR:" in msg:
                        err_summary = msg
                        break
                if not err_summary and stderr_chunks:
                    err_summary = stderr_chunks[-1]
                job.error = err_summary or f"下载失败（进程退出码 {proc.returncode}）"
                self.upload_store.delete(job.upload_id)
                return

            # 校验最终输出文件
            final_file: Path | None = None
            if dest_filepath and os.path.isfile(dest_filepath):
                final_file = Path(dest_filepath)
            else:
                # 寻找 dst_dir 下的 source.m4a 或 source.*
                for f in dst_dir.iterdir():
                    if f.is_file() and f.name.startswith("source."):
                        final_file = f
                        break

            if not final_file or not final_file.is_file() or final_file.stat().st_size == 0:
                job.status = "error"
                job.error = "下载完成但未生成有效的音频文件"
                self.upload_store.delete(job.upload_id)
                return

            file_size = final_file.stat().st_size
            job.size = file_size
            title_display = job.title or final_file.stem
            clean_filename = f"{title_display}{final_file.suffix}"
            job.filename = clean_filename

            # 写入 meta.json
            import json

            meta_data = {
                "id": job.upload_id,
                "filename": clean_filename,
                "path": str(final_file.resolve()),
                "size": file_size,
                "duration": job.duration,
                "created_at": time.time(),
                "source_url": job.url,
            }
            meta_path = dst_dir / "meta.json"
            meta_path.write_text(json.dumps(meta_data, ensure_ascii=False), encoding="utf-8")

            job.status = "ready"
            job.percent = 100.0
            job.eta = "00:00"

        except Exception as exc:
            if not job.cancel_requested and job.status != "cancelled":
                job.status = "error"
                job.error = f"处理异常: {exc}"
            self.upload_store.delete(job.upload_id)
        finally:
            self.upload_store.deactivate(job.upload_id)

