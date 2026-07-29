"""Offscreen smoke test: the window builds, tiles paint, tiers get published."""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication              # noqa: E402

from controlios.config import DeviceSpec, Registry      # noqa: E402
from controlios.vnc.session import Frame, State, Tier   # noqa: E402
from controlios.ui.grid import DeviceGrid               # noqa: E402
from controlios.ui.app import MainWindow                # noqa: E402

app = QApplication.instance() or QApplication([])


class GridTest(unittest.TestCase):
    def test_tiers_only_promote_visible_tiles(self) -> None:
        grid = DeviceGrid(tile_width=150)
        grid.resize(700, 400)
        specs = [DeviceSpec(host=f"10.0.0.{i}") for i in range(1, 101)]
        grid.set_devices(specs)
        grid.show()
        app.processEvents()

        published: dict = {}
        grid.tiers_changed.connect(published.update)
        grid._publish_tiers()

        self.assertEqual(len(published), 100)
        promoted = [k for k, t in published.items() if t is not Tier.IDLE]
        self.assertGreater(len(promoted), 0, "nothing was promoted for the viewport")
        self.assertLess(len(promoted), 100,
                        "every tile was promoted — virtualisation is not working")

    def test_frame_and_status_reach_the_tile(self) -> None:
        grid = DeviceGrid()
        spec = DeviceSpec(host="10.0.0.7")
        grid.set_devices([spec])
        frame = Frame(key=spec.key, width=4, height=8, data=bytes(4 * 8 * 3),
                      full_width=375, full_height=667)
        grid.on_frame(frame)
        grid.on_status(spec.key, State.ONLINE, "")
        tile = grid.tiles[spec.key]
        self.assertEqual(tile.state, State.ONLINE)
        self.assertIsNotNone(tile._pixmap)

    def test_selection_modes(self) -> None:
        from PySide6.QtCore import Qt

        grid = DeviceGrid()
        specs = [DeviceSpec(host=f"10.0.0.{i}") for i in range(1, 6)]
        grid.set_devices(specs)

        grid._on_tile_clicked(specs[0].key, Qt.NoModifier)
        self.assertEqual(grid.selection, [specs[0].key])
        grid._on_tile_clicked(specs[3].key, Qt.ShiftModifier)
        self.assertEqual(len(grid.selection), 4)
        grid._on_tile_clicked(specs[3].key, Qt.ControlModifier)
        self.assertEqual(len(grid.selection), 3)
        grid.clear_selection()
        self.assertEqual(grid.selection, [])


class WindowTest(unittest.TestCase):
    def test_window_builds_and_pages(self) -> None:
        registry_path = Path(__file__).parent / "_smoke_devices.json"
        registry = Registry()
        registry.merge_hosts([f"10.0.0.{i}" for i in range(1, 121)])
        registry.save(registry_path)

        window = MainWindow(registry_path)
        try:
            window.show()
            app.processEvents()
            self.assertEqual(window._pages(), 2)          # 120 devices, 100 per page
            self.assertEqual(len(window.grid.tiles), 100)
            window._go_page(1)
            self.assertEqual(len(window.grid.tiles), 20)
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)


class ScriptDialogTest(unittest.TestCase):
    """Hộp thoại kịch bản không được đụng tới mạng khi chỉ soạn thảo."""

    def setUp(self) -> None:
        from controlios.ui.app import MainWindow

        self.registry_path = Path(__file__).parent / "_script_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2"])
        registry.save(self.registry_path)
        self.window = MainWindow(self.registry_path)

    def tearDown(self) -> None:
        self.window.close()
        self.registry_path.unlink(missing_ok=True)

    def test_check_reports_syntax_error_without_running(self) -> None:
        from controlios.ui.app import ScriptDialog

        dialog = ScriptDialog(self.window)
        dialog.editor.setPlainText("tap 375 667")      # pixel, không phải tỉ lệ
        with unittest.mock.patch("controlios.ui.app.QMessageBox.warning") as warn:
            dialog._check()
        warn.assert_called_once()
        self.assertIn("0..1", warn.call_args[0][2])
        self.assertFalse(dialog.running)

    def test_check_describes_a_valid_script(self) -> None:
        from controlios.ui.app import ScriptDialog

        dialog = ScriptDialog(self.window)
        dialog.editor.setPlainText("repeat 2\n    tap 0.5 0.5\nwait 1\n")
        dialog._check()
        text = dialog.log.toPlainText()
        self.assertIn("3 lệnh", text)                  # 2 lần tap + 1 wait
        self.assertIn("lặp 2 lần", text)

    def test_run_refuses_when_nothing_is_selected(self) -> None:
        from controlios.ui.app import ScriptDialog

        dialog = ScriptDialog(self.window)
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        self.assertEqual(self.window.script_targets(), [])

        called = []
        self.window.pool.run_script = lambda *a, **k: called.append(a)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            dialog._run()
        self.assertFalse(called, "không được gửi kịch bản khi chưa chọn máy")

    def test_run_sends_parsed_steps_for_selected_devices(self) -> None:
        from controlios.ui.app import ScriptDialog

        dialog = ScriptDialog(self.window)
        dialog.editor.setPlainText("tap 0.5 0.5\nwait 0.1\n")
        self.window.grid.select_all()
        dialog.refresh_targets()

        sent = []
        self.window.pool.run_script = lambda keys, steps, folder, **kw: sent.append(
            (list(keys), steps)
        )
        dialog._run()

        self.assertEqual(len(sent), 1)
        keys, steps = sent[0]
        self.assertEqual(len(keys), 2)
        self.assertEqual([s.op for s in steps], ["tap", "wait"])
        self.assertTrue(dialog.running)
        self.assertTrue(dialog.stop_button.isEnabled())

    def test_capture_needs_a_selection(self) -> None:
        called = []
        self.window.pool.capture = lambda *a, **k: called.append(a)
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._capture_selected()
        self.assertFalse(called)

        self.window.grid.select_all()
        self.window._capture_selected()
        self.assertEqual(len(called), 1)
        self.assertEqual(len(called[0][0]), 2)

    def test_recording_toggle_starts_and_stops(self) -> None:
        started, stopped = [], []
        self.window.pool.start_recording = lambda *a, **k: (started.append(a) or "rec-1")
        self.window.pool.stop_recording = lambda rec_id=None: stopped.append(rec_id)
        self.window.grid.select_all()

        self.window.record_action.setChecked(True)
        self.assertEqual(len(started), 1)
        self.assertEqual(self.window.recording_id, "rec-1")
        self.assertIn("Dừng", self.window.record_action.text())

        self.window.record_action.setChecked(False)
        self.assertEqual(stopped, ["rec-1"])
        self.assertIsNone(self.window.recording_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
