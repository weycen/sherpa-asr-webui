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


if __name__ == "__main__":
    unittest.main()
