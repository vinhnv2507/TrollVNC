"""Đường nhanh 4 kênh: khung hình LIVE đi thẳng từ máy vào QImage.

Cắt lấy 3 kênh từ bộ đệm 4 kênh là đọc nhảy cách — đo được chậm hơn 4,6 lần so
với chép thẳng cả 4 kênh. Qt đọc được BGRA nên không cần cắt.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor                            # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

from controlios.config import DeviceSpec, Settings          # noqa: E402
from controlios.ui.image import qimage_for                  # noqa: E402
from controlios.vnc.session import (                        # noqa: E402
    PIXEL_BGRA32, PIXEL_RGB888, Frame, State, Tier, VncSession,
)
from tests.fake_vnc import FakeVncServer                    # noqa: E402

app = QApplication.instance() or QApplication([])


class PixelFormatTest(unittest.TestCase):
    def test_bgra_frame_reads_back_the_right_colour(self) -> None:
        """Sai định dạng thì ảnh vẫn hiện nhưng đỏ thành xanh — phải bắt được."""

        # Một điểm ảnh đỏ ở dạng BGRA: B=0, G=0, R=255, A=255
        frame = Frame(key="k", width=1, height=1, data=bytes([0, 0, 255, 255]),
                      full_width=1, full_height=1, pixel_format=PIXEL_BGRA32)
        image = qimage_for(frame)

        self.assertEqual(image.width(), 1)
        self.assertEqual(QColor(image.pixel(0, 0)).name(), "#ff0000")

    def test_rgb888_frame_still_works(self) -> None:
        frame = Frame(key="k", width=1, height=1, data=bytes([0, 128, 255]),
                      full_width=1, full_height=1, pixel_format=PIXEL_RGB888)
        self.assertEqual(QColor(qimage_for(frame).pixel(0, 0)).name(), "#0080ff")

    def test_bytes_per_line_matches_the_format(self) -> None:
        rgb = Frame("k", 10, 5, bytes(10 * 5 * 3), 10, 5, PIXEL_RGB888)
        bgra = Frame("k", 10, 5, bytes(10 * 5 * 4), 10, 5, PIXEL_BGRA32)
        self.assertEqual(rgb.bytes_per_line, 30)
        self.assertEqual(bgra.bytes_per_line, 40)

    def test_default_format_is_rgb888(self) -> None:
        frame = Frame("k", 2, 2, bytes(2 * 2 * 3), 2, 2)
        self.assertEqual(frame.pixel_format, PIXEL_RGB888)


class LiveFastPathTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeVncServer(width=752, height=1338)
        port = await self.server.start()
        self.frames: list[Frame] = []
        self.settings = Settings(live_fps=30.0, grid_fps=20.0, stall_timeout=5.0,
                                 reconnect_delay=0.2, live_long_edge=0)
        self.session = VncSession(
            DeviceSpec(host="127.0.0.1", port=port), self.settings,
            asyncio.Semaphore(4),
            on_frame=self.frames.append, on_status=lambda k, s, d: None,
        )

    async def asyncTearDown(self) -> None:
        await self.session.stop()
        await self.server.stop()

    async def _frame(self, tier: Tier, timeout: float = 10) -> Frame:
        self.session.set_tier(tier)
        self.session.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.frames:
                return self.frames[-1]
            await asyncio.sleep(0.05)
        self.fail("không nhận được khung hình")

    async def test_live_full_res_uses_the_four_channel_path(self) -> None:
        frame = await self._frame(Tier.LIVE)

        self.assertEqual(frame.pixel_format, PIXEL_BGRA32)
        self.assertEqual(len(frame.data), 752 * 1338 * 4)
        self.assertEqual(frame.bytes_per_line, 752 * 4)

    async def test_thumbnails_stay_three_channel(self) -> None:
        """Ảnh thu nhỏ đã ít điểm ảnh, giữ 3 kênh cho đỡ tốn bộ nhớ giao diện."""

        frame = await self._frame(Tier.GRID)

        self.assertEqual(frame.pixel_format, PIXEL_RGB888)
        self.assertEqual(len(frame.data), frame.width * frame.height * 3)

    async def test_downscaled_live_falls_back_to_three_channel(self) -> None:
        self.settings.live_long_edge = 400
        frame = await self._frame(Tier.LIVE)

        self.assertEqual(frame.pixel_format, PIXEL_RGB888)
        self.assertLessEqual(max(frame.width, frame.height), 400)

    async def test_the_image_is_usable_by_qt(self) -> None:
        frame = await self._frame(Tier.LIVE)
        image = qimage_for(frame)

        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (752, 1338))


if __name__ == "__main__":
    unittest.main(verbosity=2)
