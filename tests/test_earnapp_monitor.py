from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from controlios.config import DeviceSpec  # noqa: E402
from controlios.ui.app import Bridge, ScreenTextMonitorDialog  # noqa: E402
from controlios.ui.grid import DeviceGrid  # noqa: E402

app = QApplication.instance() or QApplication([])


class FakeWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.bridge = Bridge()
        self.grid = DeviceGrid()
        self.specs = [DeviceSpec(host="10.0.0.1"), DeviceSpec(host="10.0.0.2")]
        self.grid.set_devices(self.specs)
        self.detail = SimpleNamespace(key=self.specs[0].key)
        self.pool = SimpleNamespace(online_keys=lambda: [])
        self.registry = SimpleNamespace(devices=self.specs)


class EarnAppPerDeviceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = FakeWindow()
        self.dialog = ScreenTextMonitorDialog(self.window)
        self.dialog.scope.setCurrentIndex(self.dialog.scope.findData("opened"))
        self.dialog.run_once = lambda: None

    def tearDown(self) -> None:
        self.dialog.timer.stop()
        self.dialog.close()
        self.window.close()

    def test_can_add_and_remove_each_opened_device_independently(self) -> None:
        first, second = (spec.key for spec in self.window.specs)
        self.dialog.start_monitor()
        self.assertEqual(self.dialog.monitored_keys, {first})
        self.assertFalse(self.dialog.start_button.isEnabled())

        self.window.detail.key = second
        self.dialog.refresh_target_count()
        self.assertTrue(self.dialog.start_button.isEnabled())
        self.assertIn("Máy đang mở: TẮT", self.dialog.status.text())

        self.dialog.start_monitor()
        self.assertEqual(self.dialog.monitored_keys, {first, second})
        self.assertTrue(self.window.grid.tiles[first].monitored)
        self.assertTrue(self.window.grid.tiles[second].monitored)

        self.dialog.stop_monitor()
        self.assertEqual(self.dialog.monitored_keys, {first})
        self.assertTrue(self.window.grid.tiles[first].monitored)
        self.assertFalse(self.window.grid.tiles[second].monitored)


if __name__ == "__main__":
    unittest.main()
