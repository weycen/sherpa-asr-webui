"""Unit tests for server endpoint logic."""

import io
import unittest
from unittest.mock import MagicMock
from fastapi import HTTPException

import server


class TestServerAPI(unittest.TestCase):
    def test_list_models(self):
        data = server.list_models()
        self.assertIn("models", data)
        self.assertIn("default_model", data)
        self.assertIn("punctuations", data)

    def test_upload_unsupported_file_extension(self):
        mock_file = MagicMock()
        mock_file.filename = "test.exe"
        mock_file.file = io.BytesIO(b"dummy binary")
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            server.upload_audio(mock_request, mock_file)
        self.assertEqual(ctx.exception.status_code, 415)

    def test_upload_content_length_limit(self):
        mock_file = MagicMock()
        mock_file.filename = "test.wav"
        mock_file.file = io.BytesIO(b"dummy binary")
        mock_request = MagicMock()
        # Pretend Content-Length is 10 GB
        mock_request.headers.get.return_value = str(10 * 1024 * 1024 * 1024)

        with self.assertRaises(HTTPException) as ctx:
            server.upload_audio(mock_request, mock_file)
        self.assertEqual(ctx.exception.status_code, 413)

    def test_transcribe_duration_limit(self):
        meta = server.upload_store.save(io.BytesIO(b"RIFF dummy wav"), 1024 * 1024, "mock.wav")
        server.upload_store.update_duration(meta["id"], 5000.0)

        orig_limit = server.MAX_AUDIO_SECONDS
        server.MAX_AUDIO_SECONDS = 100.0
        try:
            with self.assertRaises(HTTPException) as ctx:
                server.transcribe_audio(upload_id=meta["id"])
            self.assertEqual(ctx.exception.status_code, 413)
            self.assertIn("超过限制", ctx.exception.detail)
        finally:
            server.MAX_AUDIO_SECONDS = orig_limit
            server.upload_store.delete(meta["id"])

    def test_get_audio(self):
        meta = server.upload_store.save(io.BytesIO(b"RIFF dummy wav"), 1024 * 1024, "mock.wav")
        try:
            res = server.get_audio(meta["id"])
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.path, meta["path"])

            # Test nonexistent id
            with self.assertRaises(HTTPException) as ctx:
                server.get_audio("b" * 32)
            self.assertEqual(ctx.exception.status_code, 404)
        finally:
            server.upload_store.delete(meta["id"])


    def test_transcribe_progress_stage(self):
        # When no job active
        res = server.transcribe_progress("nonexistent_id")
        self.assertFalse(res["active"])
        self.assertEqual(res["stage"], "idle")

        # When mock job active
        mock_job = {"cancel_event": None, "stage": "converting", "done": 1, "total": 5}
        server._set_job("mock_id", mock_job)
        try:
            res = server.transcribe_progress("mock_id")
            self.assertTrue(res["active"])
            self.assertEqual(res["stage"], "converting")
            self.assertEqual(res["done"], 1)
            self.assertEqual(res["total"], 5)
        finally:
            server._drop_job("mock_id")


    def test_lifespan(self):
        import asyncio

        async def run_lifespan():
            async with server.lifespan(server.app):
                pass

        asyncio.run(run_lifespan())

    def test_get_audio_touches_mtime(self):
        import os
        import time

        meta = server.upload_store.save(io.BytesIO(b"RIFF dummy wav"), 1024 * 1024, "mock.wav")
        try:
            up_dir = server.upload_store.root / meta["id"]
            past = time.time() - 500
            os.utime(up_dir, (past, past))
            self.assertAlmostEqual(os.path.getmtime(up_dir), past, delta=2)

            res = server.get_audio(meta["id"])
            self.assertEqual(res.status_code, 200)
            self.assertGreater(os.path.getmtime(up_dir), past + 400)
        finally:
            server.upload_store.delete(meta["id"])

    def test_list_models_includes_ytdlp(self):
        data = server.list_models()
        self.assertIn("ytdlp_available", data)

    def test_ytdlp_endpoints(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        async def run_ytdlp_tests():
            # Test invalid url
            req = server.YtDlpStartRequest(url="invalid_url")
            with self.assertRaises(HTTPException) as ctx:
                await server.ytdlp_start(req)
            self.assertEqual(ctx.exception.status_code, 400)

            # Test successful start with mock
            with patch.object(server.ytdlp_manager, "start_download", new=AsyncMock(return_value="mock_task_123")):
                req = server.YtDlpStartRequest(url="https://www.youtube.com/watch?v=123")
                res = await server.ytdlp_start(req)
                self.assertEqual(res["status"], "started")
                self.assertEqual(res["task_id"], "mock_task_123")

            # Test progress for nonexistent task
            with self.assertRaises(HTTPException) as ctx:
                await server.ytdlp_progress("nonexistent_task")
            self.assertEqual(ctx.exception.status_code, 404)

            # Test progress for existing mock job
            with patch.object(server.ytdlp_manager, "get_job", new=AsyncMock(return_value={"status": "downloading", "percent": 50.0})):
                prog = await server.ytdlp_progress("mock_task_123")
                self.assertEqual(prog["status"], "downloading")
                self.assertEqual(prog["percent"], 50.0)

            # Test cancel
            with patch.object(server.ytdlp_manager, "cancel_job", new=AsyncMock(return_value=True)):
                cancel_res = await server.ytdlp_cancel("mock_task_123")
                self.assertEqual(cancel_res["status"], "cancelled")

        asyncio.run(run_ytdlp_tests())


if __name__ == "__main__":
    unittest.main()
