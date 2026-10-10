"""Back navigation stays on the requested phone and scales to its screen."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from controlios import script
from controlios.config import DeviceSpec, Registry
from controlios.ui.app import MainWindow
from controlios.ui.branding import app_icon

app = QApplication.instance() or QApplication([])


class BackGestureTest(unittest.IsolatedAsyncioTestCase):
    async def test_edge_swipe_scales_to_portrait_landscape_and_small_streams(self):
        for width, height in ((375, 667), (667, 375), (131, 233)):
            with self.subTest(size=(width, height)):
                session = SimpleNamespace(
                    spec=DeviceSpec("test-phone"),
                    _client=SimpleNamespace(video=SimpleNamespace(width=width, height=height)),
                    swipe=AsyncMock(), tap=AsyncMock(), press_keys=AsyncMock(),
                )
                await script.run_on_session(session, script.parse("back"), lambda *args: None)
                session.swipe.assert_awaited_once()
                x1, y1, x2, y2, duration = session.swipe.call_args.args
                self.assertLessEqual(x1, width * .01)
                self.assertGreater(x2, width * .8)
                self.assertLess(x2, width)
                self.assertEqual(y1, y2)
                self.assertLess(abs(y1 - height / 2), 1)
                self.assertGreater(duration, 0)
                session.tap.assert_not_called()
                session.press_keys.assert_not_called()


class BackWindowTest(unittest.TestCase):
    def test_back_buttons_route_main_selection_and_each_large_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.json"
            registry = Registry(devices=[DeviceSpec("127.0.0.1", port=port) for port in (5901, 5902)])
            registry.save(path)
            with patch("controlios.ui.app.DevicePool"):
                window = MainWindow(path)
                window.pool.updating_ios = set()
                keys = [d.key for d in registry.devices]
                try:
                    self.assertFalse(app_icon().isNull())
                    self.assertFalse(window.windowIcon().isNull())
                    with patch.object(window, "start_script") as send:
                        window.grid._apply_selection([keys[0]])
                        window.device_gesture_buttons["back"].click()
                        self.assertEqual(send.call_args.args[1], [keys[0]])
                        self.assertEqual(send.call_args.args[0][0].args[0], "back")
                        window._open_multi_detail(keys)
                        window.multi_detail_window.panes[keys[1]].buttons["back"].click()
                        self.assertEqual(send.call_args.args[1], [keys[1]])
                        self.assertEqual(send.call_args.args[0][0].args[0], "back")
                        self.assertEqual(send.call_count, 2)
                finally:
                    window.close()
