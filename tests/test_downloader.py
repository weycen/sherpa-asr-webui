"""Unit tests for YtDlpManager and downloader utilities."""

import asyncio
import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.downloader import YtDlpManager, is_valid_web_url, sanitize_filename
from app.uploads import UploadStore


class TestDownloaderUtils(unittest.TestCase):
    def test_is_valid_web_url(self):
        self.assertTrue(is_valid_web_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(is_valid_web_url("http://example.com/video.mp4"))
        self.assertTrue(is_valid_web_url("https://bilibili.com/video/BV1xx411c7mD"))

        self.assertFalse(is_valid_web_url(""))
        self.assertFalse(is_valid_web_url(None))
        self.assertFalse(is_valid_web_url("ftp://server/file.mp3"))
        self.assertFalse(is_valid_web_url("javascript:alert(1)"))
        self.assertFalse(is_valid_web_url("file:///etc/passwd"))
        self.assertFalse(is_valid_web_url("not a url"))

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename('hello/world:test*file?"<>|'), "hello_world_test_file_____")
        self.assertEqual(sanitize_filename("   "), "audio")
        long_title = "a" * 200
        self.assertEqual(len(sanitize_filename(long_title, max_length=100)), 100)


class TestYtDlpManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = UploadStore(root=self.temp_dir.name)
        self.manager = YtDlpManager(upload_store=self.store, executable="/bin/true")

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_invalid_url_rejected(self):
        with self.assertRaises(ValueError):
            await self.manager.start_download("invalid_url")

    async def test_executable_missing_rejected(self):
        manager = YtDlpManager(upload_store=self.store, executable="/nonexistent/yt-dlp")
        with self.assertRaises(RuntimeError):
            await manager.start_download("https://www.youtube.com/watch?v=123")

    async def test_successful_mock_download(self):
        lines = [
            b"TITLE:Awesome Video\n",
            b"DURATION:45.5\n",
            b"PROGRESS: 25.0%| 1.0MiB/s| 00:03\n",
            b"PROGRESS: 75.0%| 2.5MiB/s| 00:01\n",
            b"[ExtractAudio] Destination: mock\n",
        ]

        async def mock_readline():
            if lines:
                line = lines.pop(0)
                if b"[ExtractAudio]" in line:
                    # Simulate yt-dlp/ffmpeg finishing audio extraction
                    job = self.manager._jobs[task_id]
                    dst_dir = self.store.root / job.upload_id
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    (dst_dir / "source.mp3").write_bytes(b"dummy audio content")
                return line
            return b""

        mock_proc = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            task_id = await self.manager.start_download("https://www.youtube.com/watch?v=test")
            self.assertIsNotNone(task_id)
            job = self.manager._jobs[task_id]

            # Let background task complete
            await asyncio.sleep(0.1)

            job_dict = await self.manager.get_job(task_id)
            self.assertEqual(job_dict["status"], "ready")
            self.assertEqual(job_dict["percent"], 100.0)
            self.assertEqual(job_dict["title"], "Awesome Video")
            self.assertEqual(job_dict["duration"], 45.5)
            self.assertEqual(job_dict["filename"], "Awesome Video.mp3")
            self.assertEqual(job_dict["upload_id"], job.upload_id)

            # Check meta.json written
            meta = self.store.get(job.upload_id)
            self.assertIsNotNone(meta)
            self.assertEqual(meta["filename"], "Awesome Video.mp3")

    async def test_cancel_job(self):
        mock_proc = MagicMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=asyncio.CancelledError)
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            task_id = await self.manager.start_download("https://www.youtube.com/watch?v=cancel_me")
            await asyncio.sleep(0.02)

            cancelled = await self.manager.cancel_job(task_id)
            self.assertTrue(cancelled)

            job_dict = await self.manager.get_job(task_id)
            self.assertEqual(job_dict["status"], "cancelled")

    async def test_duration_limit_exceeded(self):
        self.manager.max_audio_seconds = 30.0

        lines = [
            b"TITLE:Too Long Video\n",
            b"DURATION:120.0\n",
        ]

        async def mock_readline():
            if lines:
                return lines.pop(0)
            return b""

        mock_proc = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0
        mock_proc.terminate = MagicMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            task_id = await self.manager.start_download("https://www.youtube.com/watch?v=long")
            await asyncio.sleep(0.05)

            job_dict = await self.manager.get_job(task_id)
            self.assertEqual(job_dict["status"], "error")
            self.assertIn("超过系统上限", job_dict["error"])

    async def test_standard_progress_parsing_and_stderr(self):
        stdout_lines = [
            b"TITLE:Standard Video\n",
            b"DURATION:10.0\n",
            b"[download]  35.5% of  12.50MiB at  3.20MiB/s ETA 00:05\n",
        ]
        stderr_lines = [
            b"[download]  80.0% of  12.50MiB at  4.10MiB/s ETA 00:01\n",
            b"[ExtractAudio] Destination: mock\n",
        ]

        async def mock_stdout_readline():
            if stdout_lines:
                return stdout_lines.pop(0)
            return b""

        async def mock_stderr_readline():
            if stderr_lines:
                line = stderr_lines.pop(0)
                if b"[ExtractAudio]" in line:
                    job = self.manager._jobs[task_id]
                    dst_dir = self.store.root / job.upload_id
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    (dst_dir / "source.mp3").write_bytes(b"dummy")
                return line
            return b""

        mock_proc = MagicMock()
        mock_proc.stdout.readline = mock_stdout_readline
        mock_proc.stderr.readline = mock_stderr_readline
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            task_id = await self.manager.start_download("https://www.youtube.com/watch?v=std")
            await asyncio.sleep(0.1)

            job_dict = await self.manager.get_job(task_id)
            self.assertEqual(job_dict["status"], "ready")
            self.assertEqual(job_dict["percent"], 100.0)
            self.assertEqual(job_dict["title"], "Standard Video")


if __name__ == "__main__":
    unittest.main()
