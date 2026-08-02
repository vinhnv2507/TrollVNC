"""Chạy lệnh SSH trên nhiều máy, và bảng điều khiển SSH."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication              # noqa: E402

from controlios.config import Registry, Settings        # noqa: E402
from controlios.ui.ssh_console import PRESETS, SshConsoleDialog   # noqa: E402
from controlios.vnc.pool import DevicePool              # noqa: E402
from tests.fake_ssh import FakeSshServer                # noqa: E402

app = QApplication.instance() or QApplication([])


class SshPoolTest(unittest.TestCase):
    """Một máy jailbreak, một máy chưa — phải phân biệt được."""

    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.good = FakeSshServer()

        async def boot():
            await self.good.start()

        self.loop.run_until_complete(boot())
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        settings = Settings(ssh_port=self.good.port, ssh_user=self.good.username,
                            ssh_password=self.good.password, idle_disconnect_after=0)
        self.pool = DevicePool(settings, on_frame=lambda f: None,
                               on_status=lambda k, s, d: None)
        self.pool.start()
        self.good_key = f"127.0.0.1:{self.good.port}"

    def tearDown(self) -> None:
        self.pool.stop()
        asyncio.run_coroutine_threadsafe(self.good.stop(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

    def _run(self, command: str, keys=None):
        results, done = [], []
        self.pool.run_ssh(
            keys or [self.good_key], command,
            on_result=lambda k, r, e: results.append((k, r, e)),
            on_done=lambda d, ok, fails: done.append((ok, fails)),
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not done:
            time.sleep(0.05)
        self.assertTrue(done, "không nhận được tổng kết")
        return results, done[0]

    def test_command_output_comes_back(self) -> None:
        self.good.expect("uname", stdout="Darwin\n")

        results, (ok, failures) = self._run("uname")

        self.assertEqual(ok, 1)
        self.assertFalse(failures)
        key, result, error = results[0]
        self.assertIsNone(error)
        self.assertIn("Darwin", result.stdout)

    def test_nonzero_exit_counts_as_a_failure(self) -> None:
        self.good.expect("ls /khong-co", code=1, stderr="No such file")

        results, (ok, failures) = self._run("ls /khong-co")

        self.assertEqual(ok, 0)
        self.assertEqual(len(failures), 1)
        self.assertIn("mã 1", failures[0][1])

    def test_device_without_ssh_is_reported_not_hung(self) -> None:
        results, (ok, failures) = self._run("uname", keys=["127.0.0.2:5901"])

        self.assertEqual(ok, 0)
        self.assertEqual(len(failures), 1)
        self.assertIn("chưa jailbreak", failures[0][1])

    def test_mixed_fleet_reports_both(self) -> None:
        self.good.expect("uname", stdout="Darwin\n")

        results, (ok, failures) = self._run("uname", keys=[self.good_key, "127.0.0.2:5901"])

        self.assertEqual(ok, 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(results), 2)

    def test_ssh_available_splits_the_fleet(self) -> None:
        """Biết máy nào còn jailbreak sau lần khởi động lại gần nhất."""

        self.good.expect("true", code=0)
        done = []
        self.pool.ssh_available([self.good_key, "127.0.0.2:5901"],
                                on_done=lambda alive, dead: done.append((alive, dead)))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not done:
            time.sleep(0.05)

        alive, dead = done[0]
        self.assertEqual(alive, [self.good_key])
        self.assertEqual(dead, ["127.0.0.2:5901"])


class SshConsoleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dialog = SshConsoleDialog(3)

    def tearDown(self) -> None:
        self.dialog.close()

    def test_presets_fill_the_editor(self) -> None:
        self.dialog.presets.setCurrentIndex(2)      # bỏ qua mục "— chọn —"
        self.assertTrue(self.dialog.command())
        self.assertEqual(self.dialog.command(), self.dialog.presets.currentData())

    def test_every_preset_has_a_command(self) -> None:
        for label, command in PRESETS:
            self.assertTrue(command.strip(), label)

    def test_running_with_an_empty_command_does_nothing(self) -> None:
        asked = []
        self.dialog.run_requested.connect(asked.append)
        self.dialog.editor.setPlainText("   ")
        self.dialog._run()
        self.assertFalse(asked)
        self.assertIn("Chưa nhập lệnh", self.dialog.status.text())

    def test_running_emits_the_command_and_clears_old_results(self) -> None:
        self.dialog.add_result("10.0.0.1:5901", 0, "cu")
        asked = []
        self.dialog.run_requested.connect(asked.append)

        self.dialog.editor.setPlainText("uname -a")
        self.dialog._run()

        self.assertEqual(asked, ["uname -a"])
        self.assertEqual(self.dialog.table.rowCount(), 0, "phải xoá kết quả lần trước")
        self.assertFalse(self.dialog.run_button.isEnabled())

    def test_each_device_gets_one_row_even_when_updated_twice(self) -> None:
        self.dialog.add_result("10.0.0.1:5901", 0, "a")
        self.dialog.add_result("10.0.0.1:5901", 1, "b")
        self.assertEqual(self.dialog.table.rowCount(), 1)
        self.assertEqual(self.dialog.table.item(0, 1).text(), "1")

    def test_multiline_output_is_flattened_for_comparison(self) -> None:
        self.dialog.add_result("10.0.0.1:5901", 0, "dong1\ndong2")
        self.assertIn("⏎", self.dialog.table.item(0, 2).text())

    def test_finish_reports_counts_and_reenables(self) -> None:
        self.dialog._run() if False else None
        self.dialog.run_button.setEnabled(False)
        self.dialog.finish(2, [("10.0.0.3:5901", "loi")])
        self.assertTrue(self.dialog.run_button.isEnabled())
        self.assertIn("2/3", self.dialog.status.text())

    def test_target_label_follows_the_selection(self) -> None:
        self.dialog.set_targets(0)
        self.assertIn("Chưa chọn", self.dialog.target_label.text())
        self.dialog.set_targets(42)
        self.assertIn("42", self.dialog.target_label.text())


class WindowSshTest(unittest.TestCase):
    def setUp(self) -> None:
        from controlios.ui.app import MainWindow

        self.path = Path(__file__).parent / "_ssh_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2"])
        registry.save(self.path)
        self.window = MainWindow(self.path)

    def tearDown(self) -> None:
        self.window.close()
        self.path.unlink(missing_ok=True)

    def test_console_needs_a_selection(self) -> None:
        self.window.grid.clear_selection()
        self.window.detail.set_device(None)
        with unittest.mock.patch("controlios.ui.app.QMessageBox.information") as info:
            self.window._open_ssh_console()
        info.assert_called_once()
        self.assertIsNone(self.window.ssh_console)

    def test_running_sends_the_command_to_every_selected_device(self) -> None:
        sent = []
        self.window.pool.run_ssh = lambda keys, cmd, **kw: sent.append((list(keys), cmd))
        self.window.grid.select_all()

        self.window._run_ssh("uname -a")

        self.assertEqual(len(sent), 1)
        keys, command = sent[0]
        self.assertEqual(len(keys), 2)
        self.assertEqual(command, "uname -a")

    def test_results_reach_the_console(self) -> None:
        self.window.grid.select_all()
        self.window._open_ssh_console()

        self.window._on_ssh_result("10.0.0.1:5901", 0, "Darwin")
        self.window._on_ssh_done(1, [("10.0.0.2:5901", "chưa jailbreak")])

        self.assertEqual(self.window.ssh_console.table.rowCount(), 1)
        self.assertIn("1/2", self.window.ssh_console.status.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
