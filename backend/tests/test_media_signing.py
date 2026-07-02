import time
import unittest
from unittest.mock import patch

from core import media


class TestMediaSigning(unittest.TestCase):
    def test_sign_then_verify_roundtrip(self):
        url = media.sign(7, "screenshots", "abc.png")
        # /media/7/screenshots/abc.png?exp=...&sig=...
        self.assertTrue(url.startswith("/media/7/screenshots/abc.png?exp="))
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(url).query)
        self.assertTrue(media.verify(7, "screenshots", "abc.png", q["exp"][0], q["sig"][0]))

    def test_tampered_signature_rejected(self):
        self.assertFalse(media.verify(7, "screenshots", "abc.png", int(time.time()) + 60, "deadbeef"))

    def test_wrong_group_or_file_rejected(self):
        url = media.sign(7, "screenshots", "abc.png")
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(url).query)
        sig, exp = q["sig"][0], q["exp"][0]
        self.assertFalse(media.verify(8, "screenshots", "abc.png", exp, sig))   # other group
        self.assertFalse(media.verify(7, "screenshots", "other.png", exp, sig))  # other file
        self.assertFalse(media.verify(7, "uploads", "abc.png", exp, sig))        # other kind

    def test_expired_rejected(self):
        url = media.sign(7, "uploads", "x.png", ttl=-1)
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(url).query)
        self.assertFalse(media.verify(7, "uploads", "x.png", q["exp"][0], q["sig"][0]))

    def test_unknown_kind_rejected(self):
        self.assertFalse(media.verify(7, "secrets", "x.png", int(time.time()) + 60, "x"))

    def test_path_traversal_filename_rejected(self):
        self.assertFalse(media.is_safe_filename("../../etc/passwd"))
        self.assertFalse(media.is_safe_filename("a/b.png"))
        self.assertFalse(media.verify(7, "uploads", "../secret", int(time.time()) + 60, "x"))
        self.assertTrue(media.is_safe_filename("mcp-screenshot-uuid.png"))

    def test_presign_only_touches_media_refs(self):
        self.assertIsNone(media.presign(None))
        self.assertEqual(media.presign("/uploads/legacy.png"), "/uploads/legacy.png")
        self.assertEqual(media.presign("https://x.com/a.png"), "https://x.com/a.png")
        signed = media.presign("/media/7/screenshots/abc.png")
        self.assertTrue(signed.startswith("/media/7/screenshots/abc.png?exp="))

    def test_presign_message_in_place(self):
        msg = {"id": 1, "file_url": "/media/7/uploads/a.png"}
        out = media.presign_message(msg)
        self.assertIs(out, msg)
        self.assertIn("&sig=", msg["file_url"])
        # no file_url → untouched
        self.assertEqual(media.presign_message({"id": 2}), {"id": 2})


class TestReaper(unittest.TestCase):
    def test_reaper_purges_old_screenshots_keeps_uploads(self):
        import os
        import tempfile
        import time
        from pathlib import Path
        import skills.constants as _const
        from workspace import layout

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            orig = _const.WORKSPACE_ROOT
            _const.WORKSPACE_ROOT = root
            try:
                shots = layout.group_media_dir(7, "screenshots")
                ups = layout.group_media_dir(7, "uploads")
                shots.mkdir(parents=True, exist_ok=True)
                ups.mkdir(parents=True, exist_ok=True)

                old = shots / "old.png"
                old.write_bytes(b"x")
                stale = time.time() - 30 * 86400
                os.utime(old, (stale, stale))
                fresh = shots / "fresh.png"
                fresh.write_bytes(b"x")
                upload = ups / "keep.png"
                upload.write_bytes(b"x")
                os.utime(upload, (stale, stale))  # even an "old" upload must survive

                removed = media.reap_screenshots(max_age_days=7, max_per_group=50)

                self.assertGreaterEqual(removed, 1)
                self.assertFalse(old.exists())     # old screenshot purged
                self.assertTrue(fresh.exists())    # fresh screenshot kept
                self.assertTrue(upload.exists())   # upload never touched
            finally:
                _const.WORKSPACE_ROOT = orig

    def test_reaper_logs_when_screenshot_unlink_fails(self):
        import os
        import tempfile
        import time
        from pathlib import Path
        import skills.constants as _const
        from workspace import layout

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            orig = _const.WORKSPACE_ROOT
            _const.WORKSPACE_ROOT = root
            try:
                shots = layout.group_media_dir(7, "screenshots")
                shots.mkdir(parents=True, exist_ok=True)
                old = shots / "old.png"
                old.write_bytes(b"x")
                stale = time.time() - 30 * 86400
                os.utime(old, (stale, stale))

                orig_unlink = Path.unlink

                def fake_unlink(self, *args, **kwargs):
                    if self == old:
                        raise OSError("unlink failed")
                    return orig_unlink(self, *args, **kwargs)

                with patch("pathlib.Path.unlink", new=fake_unlink), \
                     self.assertLogs("core.media", level="ERROR") as logs:
                    removed = media.reap_screenshots(max_age_days=7, max_per_group=50)

                self.assertEqual(removed, 0)
                self.assertTrue(old.exists())
                self.assertTrue(any("failed to reap screenshot" in line for line in logs.output))
            finally:
                _const.WORKSPACE_ROOT = orig


if __name__ == "__main__":
    unittest.main()
