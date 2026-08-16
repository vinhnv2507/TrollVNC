"""Offscreen smoke test: the window builds, tiles paint, tiers get published."""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt                           # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox # noqa: E402

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

    def test_open_detail_does_not_pause_visible_grid(self) -> None:
        grid = DeviceGrid(tile_width=150)
        grid.resize(700, 400)
        specs = [DeviceSpec(host=f"10.0.0.{i}") for i in range(1, 30)]
        grid.set_devices(specs)
        grid.show()
        app.processEvents()
        grid.set_focus_key(specs[0].key)
        published = {}
        grid.tiers_changed.connect(published.update)
        grid._publish_tiers()
        visible_grid = [k for k, tier in published.items() if tier is Tier.GRID]
        self.assertTrue(visible_grid, "mở màn hình lớn không được làm đứng cả lưới")

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
    def test_first_online_applies_default_scale_once(self) -> None:
        registry_path = Path(__file__).parent / "_scale_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1"])
        registry.save(registry_path)
        window = MainWindow(registry_path)
        try:
            calls = []
            window.pool.set_scale = lambda keys, scale, **kw: calls.append(
                (list(keys), scale))
            key = "10.0.0.1:5901"
            window._on_status(key, State.ONLINE, "")
            window._on_status(key, State.ONLINE, "")
            self.assertEqual(calls, [([key], 0.35)])
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)

    def test_sort_and_manual_order_are_saved(self) -> None:
        registry_path = Path(__file__).parent / "_order_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.20", "10.0.0.3", "10.0.0.11"])
        registry.save(registry_path)
        window = MainWindow(registry_path)
        try:
            window._sort_devices("ip")
            self.assertEqual([d.host for d in Registry.load(registry_path).devices],
                             ["10.0.0.3", "10.0.0.11", "10.0.0.20"])
            window.grid.selection = ["10.0.0.11:5901"]
            window._move_selected_devices(-1)
            self.assertEqual([d.host for d in Registry.load(registry_path).devices],
                             ["10.0.0.11", "10.0.0.3", "10.0.0.20"])
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)

    def test_remove_selected_devices_updates_grid_and_saved_registry(self) -> None:
        registry_path = Path(__file__).parent / "_remove_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        registry.save(registry_path)
        window = MainWindow(registry_path)
        try:
            window.grid.selection = ["10.0.0.1:5901", "10.0.0.3:5901"]
            with unittest.mock.patch(
                    "controlios.ui.app.QMessageBox.warning",
                    return_value=QMessageBox.Yes):
                window._remove_selected_devices()
            self.assertEqual([d.host for d in window.registry.devices], ["10.0.0.2"])
            self.assertEqual([d.host for d in Registry.load(registry_path).devices],
                             ["10.0.0.2"])
            self.assertEqual(list(window.grid.tiles), ["10.0.0.2:5901"])
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)

    def test_groups_are_saved_and_filter_the_grid(self) -> None:
        registry_path = Path(__file__).parent / "_group_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        registry.save(registry_path)
        window = MainWindow(registry_path)
        try:
            window.grid.selection = ["10.0.0.1:5901", "10.0.0.3:5901"]
            self.assertTrue(window._set_selected_group("Farm A"))
            saved = Registry.load(registry_path)
            self.assertEqual([d.host for d in saved.devices if d.group == "Farm A"],
                             ["10.0.0.1", "10.0.0.3"])

            window._select_group_filter("Farm A")
            self.assertEqual(set(window.grid.tiles),
                             {"10.0.0.1:5901", "10.0.0.3:5901"})
            window._select_group_filter("")
            self.assertEqual(len(window.grid.tiles), 3)
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)

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

    def test_toolbars_fit_without_overflow_at_1900px(self) -> None:
        # Hai hàng công cụ phải chứa hết action mà không giấu sau nút » ở bề rộng
        # của một màn hình rộng phổ biến cho bảng điều khiển farm (1900px). Đã dồn
        # thêm Cài .ipa / Đẩy file / Độ sáng lên thanh công cụ nên ngưỡng nâng lên.
        # (Qt tính chỗ theo sizeHint — phồng hơn bề rộng vẽ thực; máy thật font sát
        # hơn nên còn dư nhiều.)
        from PySide6.QtWidgets import QToolBar, QToolButton

        registry_path = Path(__file__).parent / "_toolbar_devices.json"
        Registry().save(registry_path)
        window = MainWindow(registry_path)
        try:
            window.resize(1900, 700)
            window.show()
            app.processEvents()

            bars = {bar.objectName(): bar for bar in window.findChildren(QToolBar)}
            self.assertIn("navigation-toolbar", bars)
            self.assertIn("actions-toolbar", bars)
            for name in ("navigation-toolbar", "actions-toolbar"):
                bar = bars[name]
                extension = bar.findChild(QToolButton, "qt_toolbar_ext_button")
                self.assertTrue(extension is None or not extension.isVisible(),
                                f"{name} vẫn còn nút tràn »")
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)


class LayoutTest(unittest.TestCase):
    """Lưới phải chia hết bề rộng, không cắt ô, không cuộn ngang."""

    def _grid(self, count: int, width: int, height: int = 700) -> DeviceGrid:
        grid = DeviceGrid(tile_width=150)
        grid.resize(width, height)
        grid.set_devices([DeviceSpec(host=f"10.0.0.{i}") for i in range(1, count + 1)])
        grid.show()
        app.processEvents()
        grid._relayout()
        return grid

    def test_tiles_never_exceed_the_viewport_width(self) -> None:
        for width in (240, 400, 760, 1200):
            with self.subTest(width=width):
                grid = self._grid(40, width)
                columns = grid._columns
                tile = next(iter(grid.tiles.values()))
                used = tile.width() * columns + grid.SPACING * (columns - 1)
                self.assertLessEqual(
                    used, grid.viewport().width() - grid.MARGIN * 2 + columns,
                    f"ô bị tràn ra ngoài khung ở bề rộng {width}",
                )
                grid.close()

    def test_horizontal_scrolling_is_off(self) -> None:
        grid = self._grid(30, 500)
        self.assertEqual(grid.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        grid.close()

    def test_tiles_stretch_to_fill_instead_of_leaving_a_gap(self) -> None:
        """Với 6 cột, ô phải rộng ra chia hết chỗ, không giữ nguyên 150 px."""

        grid = self._grid(30, 1200)
        grid.set_columns(6)
        app.processEvents()
        tile = next(iter(grid.tiles.values()))
        available = grid.viewport().width() - grid.MARGIN * 2
        expected = (available - grid.SPACING * 5) // 6
        self.assertEqual(grid._columns, 6)
        self.assertEqual(tile.width(), expected)
        self.assertGreater(tile.width(), 150, "ô phải giãn ra, không giữ 150 px")
        grid.close()

    def test_forced_columns_beat_auto(self) -> None:
        grid = self._grid(30, 1200)
        auto_columns = grid._columns
        grid.set_columns(4)
        app.processEvents()
        self.assertEqual(grid._columns, 4)
        self.assertNotEqual(grid._columns, auto_columns)
        grid.set_columns(0)
        app.processEvents()
        self.assertEqual(grid._columns, auto_columns, "về tự động phải như ban đầu")
        grid.close()

    def test_tile_height_follows_the_real_screen_aspect(self) -> None:
        grid = self._grid(4, 600)
        tile = grid.tiles["10.0.0.1:5901"]
        before = tile.height()
        # Máy thật 752x1338 -> ô phải cao theo tỉ lệ đó.
        grid.on_frame(Frame(key="10.0.0.1:5901", width=40, height=71,
                            data=bytes(40 * 71 * 3),
                            full_width=752, full_height=1338))
        app.processEvents()
        expected = int(tile.width() / (752 / 1338)) + 22
        self.assertEqual(tile.height(), expected)
        self.assertNotEqual(tile.height(), before)
        grid.close()

    def test_detail_pane_is_only_as_wide_as_one_phone(self) -> None:
        from controlios.ui.app import MainWindow

        registry_path = Path(__file__).parent / "_layout_devices.json"
        registry = Registry()
        registry.merge_hosts([f"10.0.0.{i}" for i in range(1, 21)])
        registry.save(registry_path)

        window = MainWindow(registry_path)
        try:
            window.resize(1500, 900)
            window.show()
            app.processEvents()

            # Chưa mở máy nào: khung phải thu về mức tối thiểu, nhường chỗ lưới.
            window._fit_detail_pane()
            app.processEvents()
            self.assertEqual(window.splitter.sizes()[1], window.detail.minimumWidth())

            window._focus_device("10.0.0.1:5901")
            app.processEvents()

            grid_width, detail_width = window.splitter.sizes()
            self.assertGreater(grid_width, detail_width * 2,
                               "lưới phải chiếm phần lớn cửa sổ")
            # Vừa đúng một máy: rộng ~ cao * tỉ lệ, không phải nửa cửa sổ.
            expected = int(window.detail.height() * window.detail.aspect) + 2
            self.assertAlmostEqual(detail_width, expected, delta=6)
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)

    def test_detail_pane_refits_when_the_phone_aspect_is_known(self) -> None:
        from controlios.ui.app import MainWindow

        registry_path = Path(__file__).parent / "_layout_devices2.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1"])
        registry.save(registry_path)

        window = MainWindow(registry_path)
        try:
            window.resize(1500, 900)
            window.show()
            app.processEvents()
            window._focus_device("10.0.0.1:5901")
            app.processEvents()
            before = window.splitter.sizes()[1]

            # Máy xoay ngang: tỉ lệ đảo, khung phải rộng ra.
            window._on_frame(Frame(key="10.0.0.1:5901", width=40, height=22,
                                   data=bytes(40 * 22 * 3),
                                   full_width=1338, full_height=752))
            window._fit_detail_pane()
            app.processEvents()

            self.assertGreater(window.splitter.sizes()[1], before)
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)


class ScanDefaultsTest(unittest.TestCase):
    def test_scan_dialog_defaults_to_the_right_subnet(self) -> None:
        from controlios.ui.app import DEFAULT_SCAN_RANGE, ScanDialog

        self.assertEqual(DEFAULT_SCAN_RANGE, "172.30.2.0/24\n172.30.3.0/24")
        dialog = ScanDialog(5901)
        try:
            self.assertEqual(dialog.targets.toPlainText(),
                             "172.30.2.0/24\n172.30.3.0/24")
            self.assertEqual(dialog.port.text(), "5901")
        finally:
            dialog.close()


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
        self.assertEqual(self.window.action_targets(), [])

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

    def test_load_apps_merges_bundle_ids_from_all_selected_devices(self) -> None:
        from controlios.control_channel import AppInfo
        from controlios.ui.app import ScriptDialog

        self.window.registry.settings.control_token = "test-token"
        self.window.grid.select_all()
        replies = {
            "10.0.0.1:5901": [
                AppInfo("com.zing.zalo", "Zalo", "User", "1"),
                AppInfo("com.facebook.Facebook", "Facebook", "User", "1"),
            ],
            "10.0.0.2:5901": [
                AppInfo("com.zing.zalo", "Zalo", "User", "2"),
            ],
        }

        def list_apps(key, on_done, user_only=False):
            on_done(key, replies[key], None)

        self.window.pool.list_apps = list_apps
        dialog = ScriptDialog(self.window)
        dialog._load_script_apps()

        self.assertEqual(dialog.app_combo.count(), 2)
        labels = [dialog.app_combo.itemText(i) for i in range(dialog.app_combo.count())]
        self.assertTrue(any("Zalo" in label and "2/2 máy" in label for label in labels))
        self.assertTrue(any("Facebook" in label and "1/2 máy" in label for label in labels))

    def test_selected_app_command_is_inserted_with_bundle_id(self) -> None:
        from controlios.ui.app import ScriptDialog

        dialog = ScriptDialog(self.window)
        dialog.editor.clear()
        dialog.app_combo.addItem("Zalo — com.zing.zalo — 2/2 máy", "com.zing.zalo")
        dialog._insert_app_command("restartapp")
        self.assertEqual(dialog.editor.toPlainText(), "restartapp com.zing.zalo 1\n")

    def test_send_text_clipboard_mode_reports_content_and_paste(self) -> None:
        from controlios.ui.app import SendTextDialog

        dialog = SendTextDialog(3, self.window)
        dialog.editor.setPlainText("Bình luận đủ dấu 😀")
        # Mặc định: gõ từng phím, không dán.
        text, use_clipboard, paste = dialog.delivery()
        self.assertEqual(text, "Bình luận đủ dấu 😀")
        self.assertFalse(use_clipboard)
        self.assertFalse(paste)
        self.assertFalse(dialog.paste_after.isVisible())

        dialog.use_clipboard.setChecked(True)
        dialog.paste_after.setChecked(True)
        _, use_clipboard, paste = dialog.delivery()
        self.assertTrue(use_clipboard)
        self.assertTrue(paste)

    def test_send_text_clipboard_needs_control_token(self) -> None:
        from controlios.ui.app import SendTextDialog

        from PySide6.QtWidgets import QDialog

        self.window.registry.settings.control_token = ""
        self.window.broadcast = True          # để _targets() lấy từ selection
        self.window.grid.select_all()
        called = []
        self.window.pool.set_clipboard = lambda *a, **k: called.append(a)

        def fake_exec(dialog):
            dialog.editor.setPlainText("Nội dung")
            dialog.use_clipboard.setChecked(True)
            return QDialog.Accepted

        with unittest.mock.patch.object(SendTextDialog, "exec", fake_exec), \
                unittest.mock.patch("controlios.ui.app.QMessageBox.warning") as warn, \
                unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._send_text_dialog()
        warn.assert_called_once()
        self.assertFalse(called, "không được đặt clipboard khi thiếu control_token")

    def test_push_photo_needs_selection_and_token(self) -> None:
        called = []
        self.window.pool.push_photo = lambda *a, **k: called.append(a)
        self.window.registry.settings.control_token = "test-token"
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._push_photo()
        self.assertFalse(called)

        self.window.grid.select_all()
        with unittest.mock.patch(
            "controlios.ui.app.QFileDialog.getOpenFileName",
            return_value=("D:/anh.png", ""),
        ):
            self.window._push_photo()
        self.assertEqual(len(called), 1)
        self.assertEqual(len(called[0][0]), 2)

    def test_respring_needs_selection_and_confirmation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        called = []
        self.window.pool.respring = lambda *a, **k: called.append(a)
        self.window.registry.settings.control_token = "tok"
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._respring_selected()
        self.assertFalse(called)

        self.window.grid.select_all()
        with unittest.mock.patch("controlios.ui.app.QMessageBox.question",
                                 return_value=QMessageBox.Yes):
            self.window._respring_selected()
        self.assertEqual(len(called), 1)
        self.assertEqual(len(called[0][0]), 2)

    def test_ensure_keeper_needs_selection(self) -> None:
        called = []
        self.window.pool.ensure_keeper = lambda *a, **k: called.append(a)
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._ensure_keeper()
        self.assertFalse(called)

    def test_ensure_keeper_uses_selected_devices(self) -> None:
        called = []
        self.window.pool.ensure_keeper = lambda *a, **k: called.append(a)
        self.window.grid.select_all()
        self.window._ensure_keeper()
        self.assertEqual(len(called), 1)
        self.assertEqual(list(called[0][0]), self.window.action_targets())

    def test_keeper_watch_toggle_controls_the_timer(self) -> None:
        self.assertTrue(self.window._keeper_timer.isActive())
        self.window.keeper_watch_act.setChecked(True)
        self.assertTrue(self.window._keeper_timer.isActive())
        self.window.keeper_watch_act.setChecked(False)
        self.assertFalse(self.window._keeper_timer.isActive())

    def test_keeper_watch_tick_skips_devices_that_are_not_connected(self) -> None:
        # Máy chưa kết nối thì control socket không tới được — hỏi chỉ tổ lỗi.
        called = []
        self.window.pool.ensure_keeper = lambda *a, **k: called.append(a)
        self.window._tick_keeper_watch()
        self.assertFalse(called)

    def test_saved_scripts_round_trip_in_the_dialog(self) -> None:
        from controlios.ui.app import ScriptDialog

        scripts_path = Path(__file__).parent / "_scripts_lib.json"
        scripts_path.unlink(missing_ok=True)
        try:
            with unittest.mock.patch("controlios.config.DEFAULT_SCRIPTS", scripts_path):
                dialog = ScriptDialog(self.window)
                dialog.editor.setPlainText("tap 0.5 0.5\nwait 1\n")
                with unittest.mock.patch(
                    "controlios.ui.app.QInputDialog.getText",
                    return_value=("Kịch bản A", True),
                ):
                    dialog._save_named_script()

                # Xuất hiện trong combo của một hộp thoại mới mở.
                dialog2 = ScriptDialog(self.window)
                index = dialog2.script_combo.findData("Kịch bản A")
                self.assertGreater(index, 0)
                dialog2.script_combo.setCurrentIndex(index)
                dialog2._load_saved_script(index)
                self.assertEqual(dialog2.editor.toPlainText(), "tap 0.5 0.5\nwait 1\n")
        finally:
            scripts_path.unlink(missing_ok=True)

    def test_snapshot_dialog_lists_newest_first_and_restores(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from controlios.ui.app import SnapshotDialog
        from controlios.control_channel import Snapshot

        self.window.grid.select_all()

        def list_snaps(key, bundle, on_done):
            on_done(key, [Snapshot("sach", 1_700_000_000, 4096),
                          Snapshot("cu", 1_699_990_000, 2048)], None)

        self.window.pool.list_snapshots = list_snaps
        targets = self.window.action_targets()
        dialog = SnapshotDialog(self.window, "com.zing.zalo", targets, targets[0])
        try:
            self.assertEqual(dialog.list.count(), 2)
            self.assertEqual(dialog.list.item(0).data(Qt.UserRole), "sach")

            sent = []
            self.window.pool.restore_app = (
                lambda keys, bundle, name, **kw: sent.append((list(keys), bundle, name)))
            dialog.list.setCurrentRow(0)
            with unittest.mock.patch("controlios.ui.app.QMessageBox.warning",
                                     return_value=QMessageBox.Yes):
                dialog._restore_selected()
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][1], "com.zing.zalo")
            self.assertEqual(sent[0][2], "sach")
            self.assertEqual(len(sent[0][0]), 2)

            exported = []
            self.window.pool.export_snapshot = (
                lambda key, bundle, name, folder, **kw:
                exported.append((key, bundle, name, folder)))
            with unittest.mock.patch(
                    "controlios.ui.app.QFileDialog.getExistingDirectory",
                    return_value="D:/snapshots"):
                dialog._export_selected()
            self.assertEqual(exported[0][0], targets[0])
            self.assertEqual(exported[0][1:3], ("com.zing.zalo", "sach"))
            self.assertEqual(exported[0][3], "D:/snapshots")
        finally:
            dialog.close()

    def test_js_autoclick_dialog_push_and_run(self) -> None:
        from controlios.ui.app import JsAutoClickDialog

        self.window.grid.select_all()
        sent = []
        self.window.pool.push_and_run_autoscript = (
            lambda keys, script, **kw: sent.append((list(keys), script)))
        dialog = JsAutoClickDialog(self.window)
        try:
            dialog.editor.setPlainText("tap(0.5, 0.5);")
            dialog._push_run()
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][1], "tap(0.5, 0.5);")
            self.assertEqual(len(sent[0][0]), 2)
        finally:
            dialog.close()

    def test_command_palette_inserts_template(self) -> None:
        from controlios.ui.app import ScriptDialog

        dialog = ScriptDialog(self.window)
        dialog.editor.clear()
        idx = next(i for i in range(dialog.cmd_combo.count())
                   if (dialog.cmd_combo.itemData(i) or "").startswith("tap"))
        dialog.cmd_combo.setCurrentIndex(idx)
        dialog._insert_command_template(idx)
        self.assertIn("tap 0.5 0.85", dialog.editor.toPlainText())
        self.assertEqual(dialog.cmd_combo.currentIndex(), 0)  # trả về placeholder

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

    def test_quick_action_runs_the_gesture_on_selection(self) -> None:
        sent = []
        self.window.pool.run_script = lambda keys, steps, folder, **kw: sent.append(
            (list(keys), steps)
        )
        self.window.grid.select_all()
        self.window._run_quick_action("Về màn hình chính", "home", False)

        self.assertEqual(len(sent), 1)
        keys, steps = sent[0]
        self.assertEqual(len(keys), 2)
        self.assertEqual(steps[0].op, "macro")
        self.assertEqual(steps[0].args[0], "home")

    def test_open_app_asks_for_a_name_and_uses_it(self) -> None:
        sent = []
        self.window.pool.run_script = lambda keys, steps, folder, **kw: sent.append(steps)
        self.window.grid.select_all()

        with unittest.mock.patch(
            "controlios.ui.app.QInputDialog.getText", return_value=("Zalo", True)
        ):
            self.window._run_quick_action("Mở app…", "openapp", True)

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0].args, ("openapp", "Zalo"))

    def test_open_app_cancelled_sends_nothing(self) -> None:
        sent = []
        self.window.pool.run_script = lambda *a, **k: sent.append(a)
        self.window.grid.select_all()

        with unittest.mock.patch(
            "controlios.ui.app.QInputDialog.getText", return_value=("", False)
        ):
            self.window._run_quick_action("Mở app…", "openapp", True)
        self.assertFalse(sent)

    def test_quick_action_needs_a_selection(self) -> None:
        sent = []
        self.window.pool.run_script = lambda *a, **k: sent.append(a)
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._run_quick_action("Về màn hình chính", "home", False)
        self.assertFalse(sent)

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
