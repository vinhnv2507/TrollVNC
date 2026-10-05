import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from controlios.ios_update import inspect_package
from controlios.ui.ios_update import IOSUpdateDialog
from tests.test_ios_update import write_package

app = QApplication.instance() or QApplication([])


class IOSUpdateUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.package = inspect_package(write_package(self.root / "ControlIOS.tipa"))
        self.pool = SimpleNamespace(update_ios=Mock())
        self.settings = Mock()
        self.settings.value.return_value = ""
        self.settings_patch = patch("controlios.ui.ios_update.QSettings", return_value=self.settings)
        self.settings_patch.start()
        self.dialog = IOSUpdateDialog(self.pool, ["172.30.2.42:5901"])
        self.dialog._select(self.package)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        app.processEvents()
        self.settings_patch.stop()
        self.tmp.cleanup()

    def test_single_start_preserves_selected_targets_and_hiding_does_not_stop(self):
        self.dialog.show()
        self.dialog._start()
        self.dialog._start()
        self.pool.update_ios.assert_called_once()
        args, kwargs = self.pool.update_ios.call_args
        self.assertEqual(args, (["172.30.2.42:5901"], self.package))
        self.assertTrue(self.dialog.running)
        self.assertFalse(self.dialog.choose_file.isEnabled())
        self.dialog.close()
        self.assertTrue(self.dialog.running)
        kwargs["on_event"]("172.30.2.42:5901", "Đang cài")
        self.assertIn("Đang cài", self.dialog.log.toPlainText())
        self.assertIn("Đang cài", self.dialog.status.text())
        kwargs["on_done"]("ControlIOS 4.18", 1, [])
        self.assertFalse(self.dialog.running)
        self.assertTrue(self.dialog.start_button.isEnabled())
        self.assertIn("1/1", self.dialog.status.text())
        self.dialog._append("172.30.2.42:5901", "Thông tin bổ sung")
        self.assertIn("1/1", self.dialog.status.text())

    def test_newest_package_in_remembered_folder_selected_next_time(self):
        write_package(self.root / "next.tipa", "4.19")
        self.settings.value.side_effect = lambda key, default: str(self.root) if key.endswith("folder") else str(self.package.path)
        dialog = IOSUpdateDialog(self.pool, ["172.30.2.42:5901"])
        try:
            self.assertEqual(dialog.package.version, "4.19")
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_start_failure_does_not_leave_ui_running(self):
        self.pool.update_ios.side_effect = RuntimeError("Already running")
        with patch("controlios.ui.ios_update.QMessageBox.warning") as warning:
            self.dialog._start()
        warning.assert_called_once()
        self.assertFalse(self.dialog.running)

    def test_failed_devices_are_visible_after_completion(self):
        self.dialog._finished("ControlIOS 4.18", 0, [("172.30.2.42:5901", "TrollStore error")])
        self.assertIn("TrollStore error", self.dialog.log.toPlainText())
        self.assertIn("1 máy lỗi", self.dialog.status.text())

    def test_main_window_keeps_lan_server_alive_while_updating(self):
        from controlios.ui.app import MainWindow
        with patch("controlios.ui.app.DevicePool"):
            window = MainWindow(self.root / "devices.json")
            window.pool.updating_ios = {"172.30.2.42:5901"}
            window._update_ios = Mock()
            event = Mock()
            try:
                window.closeEvent(event)
                event.ignore.assert_called_once()
                window._update_ios.assert_called_once()
                window.pool.stop.assert_not_called()
                self.assertFalse(getattr(window, "_closing", False))
            finally:
                window.pool.updating_ios = set()
                window.close()
