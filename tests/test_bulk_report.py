"""Tổng kết thao tác hàng loạt: máy nào không làm được và vì sao."""

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

from controlios.config import DeviceSpec, Registry, Settings   # noqa: E402
from controlios.ui.app import _short_reason              # noqa: E402
from controlios.vnc.pool import DevicePool               # noqa: E402
from tests.fake_control import FakeControlServer         # noqa: E402

app = QApplication.instance() or QApplication([])


class ShortReasonTest(unittest.TestCase):
    def test_unpatched_device_gets_a_clear_reason(self) -> None:
        reason = _short_reason(
            "Máy không hiểu lệnh 'launch' — nhiều khả năng đang chạy bản "
            "TrollVNC gốc, chưa cài bản đã vá."
        )
        self.assertEqual(reason, "chưa cài bản TrollVNC đã vá")

    def test_closed_port_reason_mentions_the_patch(self) -> None:
        reason = _short_reason("172.30.3.152:46752 không phản hồi — TrollVNC chưa chạy")
        self.assertIn("chưa vá", reason)

    def test_bad_token_reason(self) -> None:
        self.assertEqual(_short_reason("Sai token. Kiểm tra secret TVNC_CTL_TOKEN"),
                         "sai token")

    def test_unknown_error_is_truncated_not_dropped(self) -> None:
        reason = _short_reason("Chuyện lạ chưa từng thấy " * 10)
        self.assertTrue(reason)
        self.assertLessEqual(len(reason), 60)


class BulkResultTest(unittest.TestCase):
    """Một máy vá rồi, một máy chưa — phải nói rõ cả hai."""

    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.good = FakeControlServer()
        self.bad = FakeControlServer()
        self.bad.unpatched = True

        async def boot():
            await self.good.start()
            await self.bad.start()

        self.loop.run_until_complete(boot())
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        settings = Settings(control_token=self.good.token,
                            control_port=self.good.port,
                            idle_disconnect_after=0)
        self.pool = DevicePool(settings, on_frame=lambda f: None,
                               on_status=lambda k, s, d: None)
        self.pool.start()
        # Không cần phiên VNC: kênh điều khiển tự dựng theo host trong key.
        self.good_key = f"127.0.0.1:{self.good.port}"
        self.bad_key = f"127.0.0.1:{self.bad.port}"

    def tearDown(self) -> None:
        self.pool.stop()

        async def shutdown():
            await asyncio.gather(self.good.stop(), self.bad.stop())

        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

    def _run(self, method, *args):
        done = []
        method(*args, on_done=lambda d, ok, fails: done.append((d, ok, fails)))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not done:
            time.sleep(0.05)
        self.assertTrue(done, "không nhận được tổng kết")
        return done[0]

    def test_launch_reports_success_and_failure_separately(self) -> None:
        describe, ok, failures = self._run(
            self.pool.launch_app, [self.good_key], "com.golike.app"
        )
        self.assertIn("com.golike.app", describe)
        self.assertEqual(ok, 1)
        self.assertFalse(failures)

    def test_unpatched_device_shows_up_as_a_failure(self) -> None:
        # Máy thứ hai dùng cổng khác nên phải đổi cấu hình cổng cho nó.
        self.pool.settings.control_port = self.bad.port
        describe, ok, failures = self._run(
            self.pool.launch_app, [self.bad_key], "com.golike.app"
        )
        self.assertEqual(ok, 0)
        self.assertEqual(len(failures), 1)
        self.assertIn("chưa cài bản đã vá", failures[0][1])

    def test_terminate_also_reports(self) -> None:
        describe, ok, failures = self._run(
            self.pool.terminate_app, [self.good_key], "com.golike.app"
        )
        self.assertIn("Đóng", describe)
        self.assertEqual(ok, 1)


class PanelFeedbackTest(unittest.TestCase):
    def setUp(self) -> None:
        from controlios.ui.app import MainWindow

        self.path = Path(__file__).parent / "_bulk_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        registry.settings.control_token = "x"
        registry.save(self.path)
        self.window = MainWindow(self.path)

    def tearDown(self) -> None:
        self.window.close()
        self.path.unlink(missing_ok=True)

    def test_selection_change_updates_the_target_label(self) -> None:
        """Trước đây nhãn đứng ở con số lúc nạp danh sách -> hiểu nhầm."""

        self.window.grid.clear_selection()
        self.assertIn("Chưa chọn", self.window.apps_panel.target_label.text())

        self.window.grid.select_all()
        self.assertIn("3", self.window.apps_panel.target_label.text())

    def test_all_failing_says_why_and_stays_on_screen(self) -> None:
        failures = [(f"10.0.0.{i}:5901",
                     "Máy không hiểu lệnh 'launch' — chưa cài bản đã vá.")
                    for i in range(1, 4)]
        self.window._on_bulk_done("Mở com.golike.app", 0, failures)

        note = self.window.apps_panel.note.text()
        self.assertIn("0/3", note)
        self.assertIn("3 máy chưa cài bản TrollVNC đã vá", note)
        self.assertIn("e5484d", self.window.apps_panel.note.styleSheet())

    def test_mixed_result_counts_both_sides(self) -> None:
        self.window._on_bulk_done(
            "Mở com.golike.app", 1,
            [("10.0.0.2:5901", "không phản hồi"), ("10.0.0.3:5901", "không phản hồi")],
        )
        note = self.window.apps_panel.note.text()
        self.assertIn("1/3", note)
        self.assertIn("2 máy", note)

    def test_full_success_is_reported_too(self) -> None:
        self.window._on_bulk_done("Mở com.golike.app", 3, [])
        note = self.window.apps_panel.note.text()
        self.assertIn("3/3", note)
        self.assertIn("3ddc84", self.window.apps_panel.note.styleSheet())

    def test_reasons_are_grouped_not_listed_per_device(self) -> None:
        """11 máy cùng một lý do thì nói một lần, đừng in 11 dòng."""

        failures = [(f"10.0.0.{i}:5901", "không phản hồi") for i in range(1, 12)]
        self.window._on_bulk_done("Mở app", 1, failures)
        note = self.window.apps_panel.note.text()
        self.assertIn("11 máy", note)
        self.assertNotIn("10.0.0.5", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
