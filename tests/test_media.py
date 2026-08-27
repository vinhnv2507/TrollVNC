"""Chuẩn hoá media cho iOS: phân loại, luật 'đã đạt chuẩn', và định tuyến transcode.

Không gọi ffmpeg thật — mock để test nhanh và tất định. File tạm ghi dưới
``tests/`` (ổ D), không dùng temp hệ thống.
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios import media                                   # noqa: E402

WORK = Path(__file__).parent / "_media_work"


class ClassifyTest(unittest.TestCase):
    def test_extension_classification(self) -> None:
        self.assertTrue(media.is_image("Anh.JPG"))
        self.assertTrue(media.is_image("x.heic"))
        self.assertFalse(media.is_image("x.mp4"))
        self.assertTrue(media.is_video("clip.MP4"))
        self.assertTrue(media.is_video("clip.mkv"))
        self.assertFalse(media.is_video("note.txt"))


class ReadyRulesTest(unittest.TestCase):
    def _ready(self, info) -> bool:
        with unittest.mock.patch.object(media, "probe_video", return_value=info):
            return media.video_is_ios_ready("x.mp4")

    def test_h264_4k_level51_rejected(self) -> None:
        self.assertFalse(self._ready(
            {"codec_name": "h264", "level": 51, "pix_fmt": "yuv420p",
             "width": 2160, "height": 3840}))

    def test_h264_1080p_level40_accepted(self) -> None:
        self.assertTrue(self._ready(
            {"codec_name": "h264", "level": 40, "pix_fmt": "yuv420p",
             "width": 1080, "height": 1920}))

    def test_hevc_4k_accepted(self) -> None:
        self.assertTrue(self._ready(
            {"codec_name": "hevc", "level": 153, "pix_fmt": "yuv420p",
             "width": 2160, "height": 3840}))

    def test_unusual_pixel_format_rejected(self) -> None:
        self.assertFalse(self._ready(
            {"codec_name": "h264", "level": 31, "pix_fmt": "yuv444p",
             "width": 1080, "height": 1920}))

    def test_unprobeable_is_not_ready(self) -> None:
        with unittest.mock.patch.object(media, "probe_video", return_value=None):
            self.assertFalse(media.video_is_ios_ready("x.mp4"))


class EnsureTest(unittest.TestCase):
    def setUp(self) -> None:
        WORK.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(WORK, ignore_errors=True)

    def test_image_returned_untouched(self) -> None:
        p = WORK / "a.jpg"
        p.write_bytes(b"x")
        self.assertEqual(media.ensure_ios_media(p, WORK / "cache"), p)

    def test_missing_tools_fails_instead_of_uploading_incompatible_video(self) -> None:
        p = WORK / "v.mkv"
        p.write_bytes(b"x")
        with unittest.mock.patch.object(media, "ffmpeg_path", return_value=None), \
                unittest.mock.patch.object(media, "ffprobe_path", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg/ffprobe"):
                media.ensure_ios_media(p, WORK / "cache")

    def test_ready_mp4_returned_untouched(self) -> None:
        p = WORK / "ok.mp4"
        p.write_bytes(b"x")
        with unittest.mock.patch.object(media, "ffmpeg_path", return_value="ffmpeg"), \
                unittest.mock.patch.object(media, "ffprobe_path", return_value="ffprobe"), \
                unittest.mock.patch.object(media, "video_is_ios_ready", return_value=True):
            self.assertEqual(media.ensure_ios_media(p, WORK / "cache"), p)

    def test_incompatible_video_is_transcoded(self) -> None:
        p = WORK / "big.mp4"
        p.write_bytes(b"x")
        cache = WORK / "cache"

        def fake_norm(src, dst) -> None:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(b"encoded")

        with unittest.mock.patch.object(media, "ffmpeg_path", return_value="ffmpeg"), \
                unittest.mock.patch.object(media, "ffprobe_path", return_value="ffprobe"), \
                unittest.mock.patch.object(media, "video_is_ios_ready", return_value=False), \
                unittest.mock.patch.object(media, "normalize_to_ios",
                                           side_effect=fake_norm) as norm:
            out = media.ensure_ios_media(p, cache)

        self.assertEqual(out, cache / "big_ios.mp4")
        self.assertTrue(out.exists())
        norm.assert_called_once()

    def test_transcode_is_cached_when_source_unchanged(self) -> None:
        p = WORK / "big.mp4"
        p.write_bytes(b"x")
        cache = WORK / "cache"
        cache.mkdir()
        dst = cache / "big_ios.mp4"
        dst.write_bytes(b"encoded")
        newer = p.stat().st_mtime + 10
        os.utime(dst, (newer, newer))

        with unittest.mock.patch.object(media, "ffmpeg_path", return_value="ffmpeg"), \
                unittest.mock.patch.object(media, "ffprobe_path", return_value="ffprobe"), \
                unittest.mock.patch.object(media, "video_is_ios_ready", return_value=False), \
                unittest.mock.patch.object(media, "normalize_to_ios") as norm:
            out = media.ensure_ios_media(p, cache)

        self.assertEqual(out, dst)
        norm.assert_not_called()

    def test_risky_image_is_normalized_to_jpeg(self) -> None:
        p = WORK / "picture.webp"
        p.write_bytes(b"x")
        cache = WORK / "cache"

        def fake_image_norm(src, dst) -> None:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(b"jpeg")

        with unittest.mock.patch.object(
                media, "normalize_image_to_ios", side_effect=fake_image_norm) as norm:
            out = media.ensure_ios_media(p, cache)

        self.assertEqual(out, cache / "picture_ios_safe.jpg")
        norm.assert_called_once_with(p, out)

    def test_force_normalizes_jpeg_after_photos_3302(self) -> None:
        p = WORK / "picture.jpg"
        p.write_bytes(b"not-a-real-jpeg")
        cache = WORK / "cache"

        def fake_image_norm(src, dst) -> None:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(b"jpeg")

        with unittest.mock.patch.object(
                media, "normalize_image_to_ios", side_effect=fake_image_norm):
            out = media.force_ios_media(p, cache)

        self.assertEqual(out.suffix, ".jpg")
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
