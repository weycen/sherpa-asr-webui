"""Unit tests for UploadStore."""

import io
import tempfile
import time
import unittest
from pathlib import Path

from app.uploads import UploadStore, is_valid_upload_id


class TestUploadStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = UploadStore(root=self.temp_dir.name, ttl_seconds=10, max_items=2)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_active_reference_counting(self):
        fake_id = "a" * 32
        self.store.activate(fake_id)
        self.store.activate(fake_id)
        self.assertEqual(self.store._active[fake_id], 2)

        self.store.deactivate(fake_id)
        self.assertEqual(self.store._active[fake_id], 1)
        active_set = {uid for uid, cnt in self.store._active.items() if cnt > 0}
        self.assertIn(fake_id, active_set)

        self.store.deactivate(fake_id)
        self.assertNotIn(fake_id, self.store._active)

    def test_save_and_prune(self):
        data = io.BytesIO(b"fake wav audio content")
        meta1 = self.store.save(data, 1024 * 1024, "audio1.wav")
        id1 = meta1["id"]
        time.sleep(0.02)

        data = io.BytesIO(b"fake wav audio content 2")
        meta2 = self.store.save(data, 1024 * 1024, "audio2.wav")
        id2 = meta2["id"]
        time.sleep(0.02)

        data = io.BytesIO(b"fake wav audio content 3")
        meta3 = self.store.save(data, 1024 * 1024, "audio3.wav")
        id3 = meta3["id"]

        # max_items is 2, so the oldest non-active item should have been pruned
        self.assertIsNone(self.store.get(id1))
        self.assertIsNotNone(self.store.get(id2))
        self.assertIsNotNone(self.store.get(id3))

    def test_prune_skips_active_items(self):
        data = io.BytesIO(b"fake wav audio content 1")
        meta1 = self.store.save(data, 1024 * 1024, "audio1.wav")
        id1 = meta1["id"]
        # Mark id1 as active
        self.store.activate(id1)

        data = io.BytesIO(b"fake wav audio content 2")
        meta2 = self.store.save(data, 1024 * 1024, "audio2.wav")
        id2 = meta2["id"]

        data = io.BytesIO(b"fake wav audio content 3")
        meta3 = self.store.save(data, 1024 * 1024, "audio3.wav")
        id3 = meta3["id"]

        # id1 is active, so it must NOT be deleted even though max_items=2
        self.assertIsNotNone(self.store.get(id1))

        self.store.deactivate(id1)

    def test_delete_security(self):
        # Invalid upload_id should not attempt to delete
        self.store.delete("../../../etc")
        self.store.delete("invalid_id")

    def test_prune_quota_bytes(self):
        # Store with small max_total_bytes = 350 bytes (one upload dir is ~230B including meta.json)
        byte_store = UploadStore(
            root=self.temp_dir.name, ttl_seconds=3600, max_items=100, max_total_bytes=350
        )
        data1 = io.BytesIO(b"A" * 60)
        meta1 = byte_store.save(data1, 1024, "a1.wav")
        time.sleep(0.02)

        data2 = io.BytesIO(b"B" * 60)
        meta2 = byte_store.save(data2, 1024, "a2.wav")

        # Two dirs (~460B) > 350 bytes, so oldest (meta1) should be pruned while meta2 is kept
        self.assertIsNone(byte_store.get(meta1["id"]))
        self.assertIsNotNone(byte_store.get(meta2["id"]))

    def test_touch_refreshes_mtime(self):
        # max_items = 2
        data1 = io.BytesIO(b"wav content 1")
        meta1 = self.store.save(data1, 1024, "a1.wav")
        id1 = meta1["id"]
        time.sleep(0.05)

        data2 = io.BytesIO(b"wav content 2")
        meta2 = self.store.save(data2, 1024, "a2.wav")
        id2 = meta2["id"]
        time.sleep(0.05)

        # Touch id1 to make it newer than id2
        self.store.touch(id1)
        time.sleep(0.02)

        # Save id3, triggering prune
        data3 = io.BytesIO(b"wav content 3")
        meta3 = self.store.save(data3, 1024, "a3.wav")
        id3 = meta3["id"]

        # Since id1 was touched, id2 was the oldest non-active and should be pruned
        self.assertIsNotNone(self.store.get(id1))
        self.assertIsNone(self.store.get(id2))
        self.assertIsNotNone(self.store.get(id3))

    def test_prune_skips_in_progress_upload(self):
        # Simulate an upload in progress: dir exists but meta.json doesn't yet
        fake_id = "f" * 32
        in_progress_dir = self.store._dir(fake_id)
        in_progress_dir.mkdir(parents=True, exist_ok=True)
        (in_progress_dir / "source.wav").write_bytes(b"partial content")

        # Running prune should NOT delete this newly created directory
        self.store.prune()
        self.assertTrue(in_progress_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
