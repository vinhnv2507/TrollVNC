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

from PySide6.QtCore import Qt                                # noqa: E402
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

    async def test_live_override_does_not_change_shared_settings(self) -> None:
        self.session.set_live_quality(7, 500)
        self.assertEqual(self.session.live_fps_override, 7)
        self.assertEqual(self.session.live_long_edge_override, 500)
        self.assertEqual(self.settings.live_fps, 30.0)
        self.assertEqual(self.settings.live_long_edge, 900)


class ScaledPixmapCacheTest(unittest.TestCase):
    """Thu phóng phải làm một lần cho mỗi khung hình, không phải mỗi lần vẽ."""

    def setUp(self) -> None:
        from controlios.ui.detail import DetailView
        from controlios.vnc.session import Frame

        self.Frame = Frame
        self.view = DetailView()
        self.view.resize(506, 890)
        self.view.set_device("10.0.0.1:5901")
        self.view.show()
        app.processEvents()
        self.view.on_frame(self._frame())
        app.processEvents()

    def tearDown(self) -> None:
        self.view.close()

    def _frame(self, key: str = "10.0.0.1:5901"):
        return self.Frame(key=key, width=100, height=178,
                          data=bytes(100 * 178 * 3),
                          full_width=752, full_height=1338)

    def test_repeated_paints_reuse_the_same_scaled_pixmap(self) -> None:
        first = self.view._scaled_pixmap()
        for _ in range(20):
            self.assertIs(self.view._scaled_pixmap(), first)

    def test_a_new_frame_invalidates_the_cache(self) -> None:
        first = self.view._scaled_pixmap()
        self.view.on_frame(self._frame())
        self.assertIsNot(self.view._scaled_pixmap(), first)

    def test_resizing_invalidates_the_cache(self) -> None:
        first = self.view._scaled_pixmap()
        self.view.resize(700, 1000)
        app.processEvents()
        second = self.view._scaled_pixmap()
        self.assertIsNot(second, first)
        self.assertLessEqual(second.height(), 1000)

    def test_mouse_moves_do_not_rescale(self) -> None:
        """Rê chuột từng gọi update() -> trước đây thu phóng lại cả khung."""

        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        first = self.view._scaled_pixmap()
        centre = self.view._target.center()
        for offset in range(20):
            event = QMouseEvent(
                QMouseEvent.Type.MouseMove,
                QPointF(centre.x() + offset, centre.y()),
                Qt.NoButton, Qt.NoButton, Qt.NoModifier,
            )
            self.view.mouseMoveEvent(event)
            app.processEvents()

        self.assertIs(self.view._scaled_pixmap(), first,
                      "rê chuột không được làm thu phóng lại")

    def test_tile_caches_too(self) -> None:
        from controlios.config import DeviceSpec
        from controlios.ui.tile import DeviceTile

        tile = DeviceTile(DeviceSpec(host="10.0.0.9"), tile_width=150)
        try:
            tile.set_frame(self._frame("10.0.0.9:5901"))
            tile.show()
            app.processEvents()
            tile.render(tile.grab())          # buộc vẽ một lần
            first = tile._scaled
            self.assertIsNotNone(first)

            tile.render(tile.grab())
            self.assertIs(tile._scaled, first)

            tile.set_frame(self._frame("10.0.0.9:5901"))
            self.assertIsNone(tile._scaled, "khung mới phải xoá ảnh nhớ sẵn")
        finally:
            tile.close()


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
        self.dialog.live_full.setChecked(False)      # mặc định là giữ ảnh gốc
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
        self.window.detail.set_device("10.0.0.1:5901")

    def tearDown(self) -> None:
        self.window.close()
        self.path.unlink(missing_ok=True)

    def test_accepting_applies_only_to_open_device(self) -> None:
        before = self.window.registry.settings.live_fps
        with unittest.mock.patch.object(QualityDialog, "exec",
                                        return_value=QDialog.Accepted), \
             unittest.mock.patch.object(QualityDialog, "apply",
                                        autospec=True) as apply_mock, \
             unittest.mock.patch.object(
                 self.window.pool, "set_live_quality") as set_live:
            self.window._open_quality_dialog()
        apply_mock.assert_called_once()
        set_live.assert_called_once_with(
            "10.0.0.1:5901", before,
            self.window.registry.settings.live_long_edge)
        self.assertEqual(Registry.load(self.path).settings.live_fps, before)

    def test_cancelling_changes_nothing(self) -> None:
        before = self.window.registry.settings.live_fps
        with unittest.mock.patch.object(QualityDialog, "exec",
                                        return_value=QDialog.Rejected):
            self.window._open_quality_dialog()
        self.assertEqual(self.window.registry.settings.live_fps, before)

    def test_dialog_loads_quality_saved_for_open_device(self) -> None:
        device = self.window.registry.devices[0]
        device.device_scale = 0.35
        device.live_fps = 7
        seen = {}

        def inspect(dialog):
            seen["scale"] = dialog.scale_value()
            seen["fps"] = dialog.live_fps.value()
            return QDialog.Rejected

        with unittest.mock.patch.object(QualityDialog, "exec", inspect):
            self.window._open_quality_dialog()
        self.assertEqual(seen, {"scale": 0.35, "fps": 7})

    def test_pool_shares_the_same_settings_object(self) -> None:
        """Đổi là ăn ngay, không phải nối lại phiên nào."""

        self.assertIs(self.window.pool.settings, self.window.registry.settings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
