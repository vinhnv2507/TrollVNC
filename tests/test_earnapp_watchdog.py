from __future__ import annotations

import asyncio
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.config import Settings
from controlios.vnc.pool import DevicePool
from controlios.vnc.session import State


class FakeOcrChannel:
    def __init__(self, texts=()):
        self.texts = set(texts)
        self.frontmost = "com.brd.earnapp"
        self.finds: list[str] = []

    async def frontmost_app(self):
        return self.frontmost

    async def find_text(self, needle: str) -> bool:
        self.finds.append(needle)
        return needle in self.texts

    async def get_color(self, rx: float, ry: float):
        return None


class FakeSession:
    def __init__(self, last_frame_at: float = 0.0, failures: int = 0) -> None:
        self.state = State.ONLINE
        self.last_frame_at = last_frame_at
        self.failures_left = failures
        self.captures = 0
        self.reconnects = 0

    def request_capture(self):
        self.captures += 1
        fut = asyncio.get_running_loop().create_future()
        if self.failures_left > 0:
            self.failures_left -= 1
            fut.set_exception(
                ConnectionError("172.30.2.50:5901 disconnected during capture")
            )
        else:
            self.last_frame_at = time.monotonic()
            fut.set_result(object())
        return fut

    def reconnect_now(self) -> None:
        self.reconnects += 1


class EarnAppWatchdogCaptureTest(unittest.TestCase):
    def run_monitor(self, session: FakeSession, texts=()):
        pool = DevicePool(Settings(), lambda *_args: None, lambda *_args: None)
        channel = FakeOcrChannel(texts)
        captured = []
        events: list[str] = []
        done = []

        async def ensure(key: str, timeout: float = 15.0):
            return session

        pool._ensure_awake = ensure  # type: ignore[method-assign]
        pool._channel = lambda _key: channel  # type: ignore[method-assign]
        pool._call_coro = lambda coro: captured.append(coro)  # type: ignore[method-assign]

        pool.monitor_text_and_restart(
            ["172.30.2.50:5901"], ("Not connected", "Connecting"),
            "com.brd.earnapp", concurrency=1, confirm_seconds=1,
            on_event=lambda _key, message: events.append(message),
            on_done=lambda total, found, failures: done.append((total, found, failures)),
        )

        async def no_sleep(_seconds: float) -> None:
            return None

        with unittest.mock.patch("controlios.vnc.pool.asyncio.sleep", no_sleep):
            asyncio.run(captured[0])
        return done[0], events, channel

    def test_live_view_skips_capture_so_disconnect_does_not_abort_ocr(self):
        session = FakeSession(last_frame_at=time.monotonic(), failures=99)
        (total, found, failures), events, channel = self.run_monitor(session)
        self.assertEqual(session.captures, 0)
        self.assertFalse(failures)
        self.assertEqual(total, 1)
        self.assertEqual(found, 0)
        self.assertTrue(channel.finds)
        self.assertFalse(any("LỖI kiểm tra màn hình" in e or "disconnected during capture" in e
                             for e in events))

    def test_retries_capture_disconnect_then_ocrs(self):
        session = FakeSession(last_frame_at=0.0, failures=1)
        (total, found, failures), events, channel = self.run_monitor(session)
        self.assertGreaterEqual(session.captures, 1)
        self.assertGreaterEqual(session.reconnects, 1)
        self.assertFalse(failures)
        self.assertTrue(channel.finds)
        self.assertTrue(any("đang nối lại" in e for e in events))

    def test_still_ocrs_when_capture_keeps_failing_but_vnc_is_online(self):
        session = FakeSession(last_frame_at=0.0, failures=99)
        (total, found, failures), events, channel = self.run_monitor(session)
        self.assertFalse(failures)
        self.assertTrue(channel.finds)
        self.assertTrue(any("OCR trên khung đang có" in e for e in events))


if __name__ == "__main__":
    unittest.main()
