"""Tuỳ chọn chất lượng và tốc độ khung hình."""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QDialog          # noqa: E402

from controlios.config import DeviceSpec, Registry, Settings  # noqa: E402
from controlios.ui.quality import PRESETS, QualityDialog      # noqa: E402
from controlios.vnc.session import State, Tier, VncSession    # noqa: E402
from tests.fake_vnc import FakeVncServer                      # noqa: E402

app = QApplication.instance() or QApplication([])


class LiveDownscaleTest(unittest.IsolatedAsyncioTestCase):
    """Tier LIVE phải thu nhỏ theo live_long_edge trước khi rời luồng mạng."""

    async def asyncSetUp(self) -> None:
        self.server = FakeVncServer(width=752, height=1338)
        port = await self.server.start()
        self.frames = []
        self.settings = Settings(live_fps=30.0, stall_timeout=5.0,
                                 reconnect_delay=0.2, live_long_edge=900)
        self.session = VncSession(
            DeviceSpec(host="127.0.0.1", port=port), self.settings,
            asyncio.Semaphore(4),
            on_frame=self.frames.append, on_status=lambda k, s, d: None,
        )

    async def asyncTearDown(self) -> None:
        await self.session.stop()
        await self.server.stop()

    async def _first_live_frame(self, timeout: float = 10):
        self.session.set_tier(Tier.LIVE)
        self.session.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.frames:
                return self.frames[-1]
            await asyncio.sleep(0.05)
        self.fail("không nhận được khung hình nào")

    async def test_live_frame_is_downscaled_to_the_limit(self) -> None:
        frame = await self._first_live_frame()

        self.assertLessEqual(max(frame.width, frame.height), 900)
        self.assertEqual(len(frame.data), frame.width * frame.height * 3)
        # Toạ độ chuột vẫn phải theo khung hình gốc, nếu không sẽ chạm sai chỗ.
        self.assertEqual((frame.full_width, frame.full_height), (752, 1338))

    async def test_downscaling_cuts_the_pixel_count(self) -> None:
        frame = await self._first_live_frame()
        full = 752 * 1338
        self.assertLess(frame.width * frame.height, full * 0.6,
                        "phải giảm đáng kể số điểm ảnh phải sao chép")

    async def test_zero_keeps_the_original_resolution(self) -> None:
        self.settings.live_long_edge = 0
        frame = await self._first_live_frame()
        self.assertEqual((frame.width, frame.height), (752, 1338))

    async def test_limit_above_the_screen_changes_nothing(self) -> None:
        self.settings.live_long_edge = 4000
        frame = await self._first_live_frame()
        self.assertEqual((frame.width, frame.height), (752, 1338))


class QualityDialogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.dialog = QualityDialog(self.settings)

    def tearDown(self) -> None:
        self.dialog.close()

    def test_loads_current_values(self) -> None:
        self.assertEqual(self.dialog.live_fps.value(), self.settings.live_fps)
        self.assertEqual(self.dialog.grid_fps.value(), self.settings.grid_fps)
        self.assertEqual(self.dialog.thumb_edge.value(), self.settings.thumb_long_edge)

    def test_apply_writes_back(self) -> None:
        self.dialog.live_fps.setValue(6)
        self.dialog.grid_fps.setValue(0.4)
        self.dialog.thumb_edge.setValue(200)
        self.dialog.live_edge.setValue(500)
        self.dialog.apply()

        self.assertEqual(self.settings.live_fps, 6)
        self.assertEqual(self.settings.grid_fps, 0.4)
        self.assertEqual(self.settings.thumb_long_edge, 200)
        self.assertEqual(self.settings.live_long_edge, 500)

    def test_full_resolution_checkbox_means_zero(self) -> None:
        self.dialog.live_full.setChecked(True)
        self.dialog.apply()
        self.assertEqual(self.settings.live_long_edge, 0)
        self.assertFalse(self.dialog.live_edge.isEnabled(),
                         "chọn độ phân giải gốc thì ô độ nét phải mờ đi")

    def test_presets_change_every_field(self) -> None:
        smooth = PRESETS[0]
        self.dialog._apply_preset(*smooth[1:])
        self.dialog.apply()

        self.assertEqual(self.settings.live_fps, smooth[1])
        self.assertEqual(self.settings.live_long_edge, smooth[2])
        self.assertEqual(self.settings.grid_fps, smooth[3])
        self.assertEqual(self.settings.thumb_long_edge, smooth[4])

    def test_sharpest_preset_uses_native_resolution(self) -> None:
        self.dialog._apply_preset(*PRESETS[-1][1:])
        self.dialog.apply()
        self.assertEqual(self.settings.live_long_edge, 0)
        self.assertTrue(self.dialog.live_full.isChecked())

    def test_presets_are_ordered_light_to_heavy(self) -> None:
        fps = [p[1] for p in PRESETS]
        self.assertEqual(fps, sorted(fps), "mẫu phải xếp từ nhẹ tới nặng")

    def test_idle_zero_shows_a_words_not_a_number(self) -> None:
        self.dialog.idle_after.setValue(0)
        self.assertIn("không bao giờ", self.dialog.idle_after.text())


class WindowQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        from controlios.ui.app import MainWindow

        self.path = Path(__file__).parent / "_quality_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1"])
        registry.save(self.path)
        self.window = MainWindow(self.path)

    def tearDown(self) -> None:
        self.window.close()
        self.path.unlink(missing_ok=True)

    def test_accepting_applies_and_saves(self) -> None:
        with unittest.mock.patch.object(QualityDialog, "exec",
                                        return_value=QDialog.Accepted), \
             unittest.mock.patch.object(QualityDialog, "apply",
                                        autospec=True) as apply_mock:
            self.window._open_quality_dialog()
        apply_mock.assert_called_once()

        # Đã ghi lại xuống đĩa để lần mở sau vẫn giữ.
        saved = Registry.load(self.path)
        self.assertEqual(saved.settings.live_fps, self.window.registry.settings.live_fps)

    def test_cancelling_changes_nothing(self) -> None:
        before = self.window.registry.settings.live_fps
        with unittest.mock.patch.object(QualityDialog, "exec",
                                        return_value=QDialog.Rejected):
            self.window._open_quality_dialog()
        self.assertEqual(self.window.registry.settings.live_fps, before)

    def test_pool_shares_the_same_settings_object(self) -> None:
        """Đổi là ăn ngay, không phải nối lại phiên nào."""

        self.assertIs(self.window.pool.settings, self.window.registry.settings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
