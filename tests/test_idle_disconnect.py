"""Ngắt kết nối tới máy ngoài khung nhìn, để iPhone ngừng chụp hình.

TrollVNC chỉ chạy ScreenCapturer khi còn client nối vào, nên tier IDLE không
cứu được CPU của máy — phải rời hẳn.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.config import DeviceSpec, Settings          # noqa: E402
from controlios.vnc.pool import DevicePool                  # noqa: E402
from controlios.vnc.session import Frame, State, Tier       # noqa: E402
from tests.fake_vnc import FakeVncServer                    # noqa: E402


class IdleDisconnectTest(unittest.TestCase):
    """Pool chạy loop riêng ở thread nền — gọi từ code đồng bộ như UI."""

    DEVICES = 3

    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.servers: list[FakeVncServer] = []

        async def boot():
            for _ in range(self.DEVICES):
                server = FakeVncServer()
                await server.start()
                self.servers.append(server)

        self.loop.run_until_complete(boot())
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        self.settings = Settings(
            grid_fps=20.0, stall_timeout=5.0, reconnect_delay=0.2,
            connect_concurrency=8, idle_disconnect_after=1.0,
        )
        self.frames: list[Frame] = []
        self.pool = DevicePool(self.settings, on_frame=self.frames.append,
                               on_status=lambda k, s, d: None)
        self.pool.start()
        self.specs = [DeviceSpec(host="127.0.0.1", port=s.port) for s in self.servers]
        self.pool.set_devices(self.specs)
        self.assertTrue(self.wait_until(
            lambda: self.pool.stats()["online"] == self.DEVICES, 20), "chưa online hết")

    def tearDown(self) -> None:
        self.pool.stop()

        async def shutdown():
            await asyncio.gather(*(s.stop() for s in self.servers))

        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

    @staticmethod
    def wait_until(predicate, timeout: float = 15) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def _connections(self) -> int:
        return sum(s.connections for s in self.servers)

    def test_idle_devices_disconnect_so_the_phone_stops_capturing(self) -> None:
        self.pool.set_tiers({spec.key: Tier.IDLE for spec in self.specs})

        self.assertTrue(self.wait_until(
            lambda: self.pool.stats()["dormant"] == self.DEVICES, 20),
            f"chưa ngủ hết: {self.pool.stats()}")
        self.assertEqual(self.pool.stats()["online"], 0)

    def test_visible_devices_stay_connected(self) -> None:
        tiers = {spec.key: Tier.IDLE for spec in self.specs}
        tiers[self.specs[0].key] = Tier.GRID
        self.pool.set_tiers(tiers)

        self.assertTrue(self.wait_until(
            lambda: self.pool.stats()["dormant"] == self.DEVICES - 1, 20))
        # Máy đang nhìn thấy không được ngủ.
        self.assertEqual(self.pool.stats()["online"], 1)

    def test_scrolling_back_wakes_the_device(self) -> None:
        self.pool.set_tiers({spec.key: Tier.IDLE for spec in self.specs})
        self.assertTrue(self.wait_until(
            lambda: self.pool.stats()["dormant"] == self.DEVICES, 20))
        before = self._connections()

        # Cuộn tới: một máy được nhìn lại.
        tiers = {spec.key: Tier.IDLE for spec in self.specs}
        tiers[self.specs[1].key] = Tier.GRID
        self.pool.set_tiers(tiers)

        self.assertTrue(self.wait_until(lambda: self.pool.stats()["online"] == 1, 20),
                        "máy không tỉnh lại khi cuộn tới")
        self.assertGreater(self._connections(), before, "phải mở kết nối mới")

    def test_capture_wakes_a_dormant_device(self) -> None:
        import tempfile

        self.pool.set_tiers({spec.key: Tier.IDLE for spec in self.specs})
        self.assertTrue(self.wait_until(
            lambda: self.pool.stats()["dormant"] == self.DEVICES, 20))

        with tempfile.TemporaryDirectory() as tmp:
            results: list[tuple] = []
            self.pool.capture([self.specs[0].key], tmp,
                              on_event=lambda k, p, e: results.append((k, p, e)))
            self.assertTrue(self.wait_until(lambda: results, 25), "chụp ảnh treo")

            key, path, error = results[0]
            self.assertIsNone(error, f"chụp máy đang ngủ phải chạy được: {error}")
            self.assertTrue(Path(path).exists())

    def test_script_wakes_dormant_devices_instead_of_skipping(self) -> None:
        import tempfile

        from controlios import script

        self.pool.set_tiers({spec.key: Tier.IDLE for spec in self.specs})
        self.assertTrue(self.wait_until(
            lambda: self.pool.stats()["dormant"] == self.DEVICES, 20))

        steps = script.parse("tap 0.5 0.5")
        with tempfile.TemporaryDirectory() as tmp:
            events: list[tuple] = []
            done: list[bool] = []
            self.pool.run_script([s.key for s in self.specs], steps, tmp,
                                 on_event=lambda k, m: events.append((k, m)),
                                 on_done=lambda: done.append(True))
            self.assertTrue(self.wait_until(lambda: done, 30), f"kịch bản treo: {events}")

            skipped = [e for e in events if "bỏ qua" in e[1]]
            self.assertFalse(skipped, f"không được bỏ qua máy đang ngủ: {skipped}")
            finished = [e for e in events if e[1] == "xong"]
            self.assertEqual(len(finished), self.DEVICES)

    def test_zero_disables_the_policy(self) -> None:
        self.settings.idle_disconnect_after = 0
        self.pool.set_tiers({spec.key: Tier.IDLE for spec in self.specs})

        time.sleep(3)
        self.assertEqual(self.pool.stats()["dormant"], 0,
                         "đặt 0 thì phải giữ nguyên hành vi cũ")
        self.assertEqual(self.pool.stats()["online"], self.DEVICES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
