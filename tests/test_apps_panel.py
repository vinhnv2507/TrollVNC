"""Bảng ứng dụng trong giao diện."""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt                           # noqa: E402
from PySide6.QtWidgets import (QApplication, QMessageBox, QPlainTextEdit,
                               QSizePolicy)  # noqa: E402

from controlios.config import Registry                  # noqa: E402
from controlios.control_channel import AppInfo          # noqa: E402
from controlios.ui.apps_panel import (                      # noqa: E402
    AppsPanel, colour_index, letter_icon,
)

app = QApplication.instance() or QApplication([])

SAMPLE = [
    AppInfo("com.facebook.Facebook", "Facebooku", "User", "480.0"),
    AppInfo("com.golike.app", "GoLike", "User", "2.1.0"),
    AppInfo("io.grass.app", "Grass", "User", "1.4"),
    AppInfo("com.honeygain.app", "Honeygain", "User", "3.0"),
    AppInfo("com.apple.Preferences", "Cài đặt", "System", "1.0"),
]


class AppsPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = AppsPanel()
        self.panel.set_apps(SAMPLE)

    def tearDown(self) -> None:
        self.panel.close()

    def test_user_only_is_on_by_default(self) -> None:
        self.assertTrue(self.panel.user_only.isChecked())
        names = [self.panel.list.item(i).text() for i in range(self.panel.list.count())]
        self.assertNotIn("Cài đặt", names, "app hệ thống phải bị ẩn theo mặc định")
        self.assertIn("GoLike", names)

    def test_unchecking_reveals_system_apps(self) -> None:
        self.panel.user_only.setChecked(False)
        names = [self.panel.list.item(i).text() for i in range(self.panel.list.count())]
        self.assertIn("Cài đặt", names)

    def test_filter_matches_name_and_bundle_id(self) -> None:
        self.panel.filter.setText("honey")
        self.assertEqual(self.panel.list.count(), 1)
        self.assertEqual(self.panel.list.item(0).text(), "Honeygain")

        self.panel.filter.setText("io.grass")
        self.assertEqual(self.panel.list.count(), 1)
        self.assertEqual(self.panel.list.item(0).text(), "Grass")

    def test_filter_is_case_insensitive(self) -> None:
        self.panel.filter.setText("GOLIKE")
        self.assertEqual(self.panel.list.count(), 1)

    def test_clicking_an_app_asks_to_launch_its_bundle_id(self) -> None:
        launched = []
        self.panel.launch_requested.connect(launched.append)
        item = self.panel.list.item(0)
        self.panel._on_activated(item)
        self.assertEqual(launched, [item.data(Qt.UserRole)])
        self.assertTrue(launched[0].count(".") >= 1, "phải là bundle id, không phải tên")

    def test_data_buttons_emit_for_the_selected_app(self) -> None:
        got = {}
        self.panel.snapshot_requested.connect(lambda b: got.setdefault("snap", b))
        self.panel.backup_pc_requested.connect(lambda b: got.setdefault("backup", b))
        self.panel.restore_requested.connect(lambda b: got.setdefault("restore", b))
        self.panel.wipe_requested.connect(lambda b: got.setdefault("wipe", b))

        self.panel.list.setCurrentRow(0)
        bundle = self.panel.list.item(0).data(Qt.UserRole)

        self.panel.snapshot_button.click()
        self.panel.backup_pc_button.click()
        self.panel.restore_button.click()
        self.panel.wipe_button.click()
        self.assertEqual(got, {"snap": bundle, "backup": bundle,
                               "restore": bundle, "wipe": bundle})

    def test_data_buttons_without_selection_hint_instead_of_emitting(self) -> None:
        fired = []
        self.panel.snapshot_requested.connect(fired.append)
        self.panel.list.clearSelection()
        self.panel.list.setCurrentRow(-1)
        self.panel.snapshot_button.click()
        self.assertFalse(fired)
        self.assertIn("chọn một app", self.panel.status.text().lower())

    def test_restart_signal_carries_selected_bundle(self) -> None:
        fired = []
        self.panel.restart_requested.connect(fired.append)
        bundle = self.panel.list.item(0).data(Qt.UserRole)
        self.panel.restart_requested.emit(bundle)
        self.assertEqual(fired, [bundle])

    def test_each_item_carries_its_bundle_id_and_tooltip(self) -> None:
        item = self.panel.list.item(0)
        self.assertIn(".", item.data(Qt.UserRole))
        self.assertIn(item.data(Qt.UserRole), item.toolTip())
        self.assertIn("Loại", item.toolTip())

    def test_status_counts_visible_versus_total(self) -> None:
        self.panel.filter.setText("gr")
        self.assertIn("1/5", self.panel.status.text())

    def test_error_clears_the_list_and_shows_message(self) -> None:
        self.panel.set_error("máy không phản hồi")
        self.assertEqual(self.panel.list.count(), 0)
        self.assertIn("không phản hồi", self.panel.status.text())
        self.assertTrue(self.panel.refresh_button.isEnabled())

    def test_clear_log_button_clears_operation_note(self) -> None:
        self.panel.set_note("Keeper check failed", error=True)
        self.assertTrue(self.panel.note.text())
        self.panel.clear_log_button.click()
        self.assertEqual(self.panel.note.text(), "")

    def test_operation_log_does_not_expand_apps_dock_width(self) -> None:
        self.panel.set_note("172.30.3.1:5901: " + "very-long-error " * 100, error=True)
        self.assertEqual(self.panel.note.lineWrapMode(), QPlainTextEdit.NoWrap)
        self.assertEqual(self.panel.note.horizontalScrollBarPolicy(),
                         Qt.ScrollBarAsNeeded)
        self.assertEqual(self.panel.note.sizePolicy().horizontalPolicy(),
                         QSizePolicy.Ignored)

    def test_loading_disables_refresh_until_result(self) -> None:
        self.panel.set_loading()
        self.assertFalse(self.panel.refresh_button.isEnabled())
        self.panel.set_apps(SAMPLE)
        self.assertTrue(self.panel.refresh_button.isEnabled())

    def test_target_label_reflects_selection_size(self) -> None:
        self.panel.set_targets(0)
        self.assertIn("Chưa chọn", self.panel.target_label.text())
        self.panel.set_targets(1)
        self.assertIn("máy đang mở", self.panel.target_label.text())
        self.panel.set_targets(42)
        self.assertIn("42", self.panel.target_label.text())

    def test_icon_is_identical_for_the_same_app(self) -> None:
        first = letter_icon(SAMPLE[0]).pixmap(34, 34).toImage()
        again = letter_icon(SAMPLE[0]).pixmap(34, 34).toImage()
        self.assertEqual(first, again, "cùng app phải ra cùng biểu tượng")

    def test_icon_colour_survives_a_restart(self) -> None:
        """Màu phải cố định theo bundle id.

        Dùng hash() của Python thì màu đổi mỗi lần mở phần mềm, nhìn như hỏng.
        Các giá trị dưới đây tính bằng CRC32 nên không đổi giữa các lần chạy.
        """

        self.assertEqual(colour_index("com.facebook.Facebook"), 8)
        self.assertEqual(colour_index("com.golike.app"), 5)
        self.assertEqual(colour_index("io.grass.app"), 4)
        self.assertEqual(colour_index("com.honeygain.app"), 4)


class WindowIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        from controlios.ui.app import MainWindow

        self.path = Path(__file__).parent / "_apps_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2"])
        registry.save(self.path)
        self.window = MainWindow(self.path)

    def tearDown(self) -> None:
        self.window.close()
        self.path.unlink(missing_ok=True)

    def test_panel_is_hidden_until_asked_for(self) -> None:
        self.assertFalse(self.window.apps_dock.isVisible())

    def test_opening_apps_panel_automatically_loads_apps(self) -> None:
        self.window.registry.settings.control_token = "token"
        self.window.grid.selection = ["10.0.0.1:5901"]
        called = []
        self.window.pool.list_apps = lambda key, on_done: called.append(key)
        self.window._on_apps_panel_toggled(True)
        self.assertEqual(called, ["10.0.0.1:5901"])
        self.assertTrue(self.window.apps_panel.user_only.isChecked())

    def test_reload_without_token_explains_instead_of_hanging(self) -> None:
        self.window.registry.settings.control_token = ""
        self.window.grid.select_all()
        self.window.detail.set_device("10.0.0.1:5901")
        called = []
        self.window.pool.list_apps = lambda *a, **k: called.append(a)

        self.window._reload_apps()

        self.assertFalse(called, "không được gọi ra mạng khi chưa có token")
        self.assertIn("control_token", self.window.apps_panel.status.text())

    def test_reload_without_a_device_explains(self) -> None:
        self.window.registry.settings.control_token = "x"
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        called = []
        self.window.pool.list_apps = lambda *a, **k: called.append(a)

        self.window._reload_apps()

        self.assertFalse(called)
        self.assertIn("Chưa chọn máy", self.window.apps_panel.status.text())

    def test_reload_asks_the_pool_when_configured(self) -> None:
        self.window.registry.settings.control_token = "Congavinh1"
        self.window.detail.set_device("10.0.0.1:5901")
        called = []
        self.window.pool.list_apps = lambda key, on_done: called.append(key)

        self.window._reload_apps()

        self.assertEqual(called, ["10.0.0.1:5901"])

    def test_launch_goes_to_every_selected_device(self) -> None:
        sent = []
        self.window.pool.launch_app = lambda keys, bundle, **kw: sent.append(
            (list(keys), bundle)
        )
        self.window.grid.select_all()
        self.window._launch_app("com.golike.app")

        self.assertEqual(len(sent), 1)
        keys, bundle = sent[0]
        self.assertEqual(len(keys), 2)
        self.assertEqual(bundle, "com.golike.app")

    def test_terminate_goes_to_every_selected_device(self) -> None:
        sent = []
        self.window.pool.terminate_app = lambda keys, bundle, **kw: sent.append(bundle)
        self.window.grid.select_all()
        self.window._terminate_app("com.golike.app")
        self.assertEqual(sent, ["com.golike.app"])

    def test_restart_goes_to_selected_devices_with_random_delay_range(self) -> None:
        sent = []
        self.window.pool.restart_app = (
            lambda keys, bundle, min_delay, max_delay, **kw:
            sent.append((list(keys), bundle, min_delay, max_delay)))
        self.window.grid.select_all()
        self.window._restart_app("com.brd.earnapp")
        self.assertEqual(len(sent[0][0]), 2)
        self.assertEqual(sent[0][1:], ("com.brd.earnapp", 3.0, 5.0))

    def test_launch_without_selection_is_refused(self) -> None:
        sent = []
        self.window.pool.launch_app = lambda *a, **k: sent.append(a)
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._launch_app("com.golike.app")
        self.assertFalse(sent)

    def test_device_gesture_from_the_panel_runs_a_script(self) -> None:
        sent = []
        self.window.pool.run_script = lambda keys, steps, folder, **kw: sent.append(
            (list(keys), steps)
        )
        self.window.grid.select_all()
        self.window.device_gesture_buttons["home"].click()

        self.assertEqual(len(sent), 1)
        keys, steps = sent[0]
        self.assertEqual(len(keys), 2, "cử chỉ phải chạy trên mọi máy đang chọn")
        self.assertEqual(steps[0].args[0], "home")

    def test_lock_button_uses_the_power_gesture(self) -> None:
        sent = []
        self.window.pool.run_script = lambda keys, steps, folder, **kw: sent.append(steps)
        self.window.grid.select_all()
        self.window.device_gesture_buttons["lock"].click()
        self.assertEqual(sent[0][0].args[0], "lock")

    def test_old_quick_action_toolbar_button_is_gone(self) -> None:
        """Thao tác app đã gộp vào bảng Ứng dụng, không còn nút riêng."""

        self.assertFalse(hasattr(self.window, "quick_button"))
        labels = [a.text() for a in self.window.findChildren(type(self.window.apps_action))]
        self.assertNotIn("Thao tác app ▾", labels)

    def test_install_ipa_needs_a_selection(self) -> None:
        sent = []
        self.window.pool.install_ipa = lambda *a, **k: sent.append(a)
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information"):
            self.window._install_ipa()
        self.assertFalse(sent)

    def test_install_ipa_asks_before_touching_every_device(self) -> None:
        """Cài app hàng loạt là việc nặng — không được làm khi chưa xác nhận."""

        sent = []
        self.window.pool.install_ipa = lambda keys, ipa, **k: sent.append((list(keys), ipa))
        self.window.grid.select_all()

        ipa = Path(self.path.parent) / "_fake.ipa"
        ipa.write_bytes(b"PK\x03\x04fake")
        try:
            with unittest.mock.patch("controlios.ui.app.QFileDialog.getOpenFileName",
                                     return_value=(str(ipa), "")), \
                 unittest.mock.patch("controlios.ui.app.QMessageBox.question",
                                     return_value=unittest.mock.sentinel.no):
                self.window._install_ipa()
            self.assertFalse(sent, "từ chối xác nhận thì không được cài")

            with unittest.mock.patch("controlios.ui.app.QFileDialog.getOpenFileName",
                                     return_value=(str(ipa), "")), \
                 unittest.mock.patch("controlios.ui.app.QMessageBox.question",
                                     return_value=QMessageBox.Yes):
                self.window._install_ipa()
            self.assertEqual(len(sent), 1)
            keys, chosen = sent[0]
            self.assertEqual(len(keys), 2)
            self.assertEqual(Path(chosen).name, "_fake.ipa")
        finally:
            ipa.unlink(missing_ok=True)

    def test_push_file_sends_local_and_remote_paths(self) -> None:
        sent = []
        self.window.pool.push_file = lambda keys, local, remote, **k: sent.append(
            (list(keys), Path(local).name, remote)
        )
        self.window.grid.select_all()

        with unittest.mock.patch("controlios.ui.app.QFileDialog.getOpenFileName",
                                 return_value=(r"C:\tmp\anh.jpg", "")), \
             unittest.mock.patch("controlios.ui.app.QInputDialog.getText",
                                 return_value=("/var/mobile/Documents/anh.jpg", True)):
            self.window._push_file()

        self.assertEqual(sent, [(sent[0][0], "anh.jpg", "/var/mobile/Documents/anh.jpg")])
        self.assertEqual(len(sent[0][0]), 2)

    def test_push_file_cancelled_sends_nothing(self) -> None:
        sent = []
        self.window.pool.push_file = lambda *a, **k: sent.append(a)
        self.window.grid.select_all()

        with unittest.mock.patch("controlios.ui.app.QFileDialog.getOpenFileName",
                                 return_value=("", "")):
            self.window._push_file()
        self.assertFalse(sent)

    def test_error_from_the_network_thread_reaches_the_panel(self) -> None:
        self.window._apply_apps("10.0.0.1:5901", [], "máy không phản hồi")
        self.assertIn("không phản hồi", self.window.apps_panel.status.text())

    def test_apps_from_the_network_thread_reach_the_panel(self) -> None:
        self.window._apply_apps("10.0.0.1:5901", SAMPLE, "")
        self.assertGreater(self.window.apps_panel.list.count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
