"""End-to-end checks against the fake RFB server (no phones needed)."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.config import DeviceSpec, Settings          # noqa: E402
from controlios.vnc.pool import DevicePool                  # noqa: E402
from controlios.vnc.session import Frame, State, Tier, VncSession  # noqa: E402
from tests.fake_vnc import FakeVncServer                    # noqa: E402


def fast_settings(**kw) -> Settings:
    settings = Settings(grid_fps=20.0, live_fps=40.0, stall_timeout=5.0,
                        reconnect_delay=0.2, connect_concurrency=8)
    for key, value in kw.items():
        setattr(settings, key, value)
    return settings


class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeVncServer()
        self.port = await self.server.start()
        self.frames: list[Frame] = []
        self.states: list[tuple[str, State]] = []

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def make_session(self, settings=None) -> VncSession:
        return VncSession(
            DeviceSpec(host="127.0.0.1", port=self.port),
            settings or fast_settings(),
            asyncio.Semaphore(4),
            on_frame=self.frames.append,
            on_status=lambda k, s, d: self.states.append((k, s)),
        )

    async def wait_for(self, predicate, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.05)
        return False

    async def test_idle_session_takes_one_frame_then_goes_quiet(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.IDLE)
        session.start()

        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 1),
                        "no first frame arrived")
        self.assertEqual(session.state, State.ONLINE)

        # An idle session must stop asking for pixels after its first frame.
        requests = self.server.update_requests
        await asyncio.sleep(1.0)
        self.assertEqual(self.server.update_requests, requests,
                         "idle session kept requesting framebuffer updates")

        await session.stop()

    async def test_grid_tier_streams_and_thumbnails_are_small(self) -> None:
        settings = fast_settings(thumb_long_edge=160)
        session = self.make_session(settings)
        session.set_tier(Tier.GRID)
        session.start()

        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 3),
                        "grid tier did not stream")
        frame = self.frames[-1]
        self.assertLessEqual(max(frame.width, frame.height), 400)
        self.assertEqual(len(frame.data), frame.width * frame.height * 3)
        self.assertEqual((frame.full_width, frame.full_height),
                         (self.server.width, self.server.height))

        await session.stop()

    async def test_live_tier_delivers_full_resolution(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.LIVE)
        session.start()

        self.assertTrue(await self.wait_for(
            lambda: any(f.width == self.server.width for f in self.frames)),
            "live tier never produced a full-resolution frame")

        await session.stop()

    async def test_promoting_from_idle_resumes_streaming(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.IDLE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 1))

        before = self.server.update_requests
        await asyncio.sleep(0.5)
        session.set_tier(Tier.GRID)
        self.assertTrue(await self.wait_for(
            lambda: self.server.update_requests > before + 2),
            "promotion from IDLE did not resume updates")

        await session.stop()

    async def test_input_reaches_the_server(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.GRID)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 1))

        session.tap(120, 240)
        session.type_text("ab")
        self.assertTrue(await self.wait_for(
            lambda: len(self.server.pointer_events) >= 3 and len(self.server.key_events) >= 4))

        moves = [e for e in self.server.pointer_events if e[1:] == (120, 240)]
        self.assertTrue(moves, f"pointer never landed on (120,240): {self.server.pointer_events}")
        self.assertTrue(any(buttons == 1 for buttons, _, _ in moves), "no button press seen")

        await session.stop()

    async def test_reconnects_after_server_drop(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.GRID)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))

        await self.server.stop()
        self.assertTrue(await self.wait_for(lambda: session.state is not State.ONLINE, 6.0))

        await self.server.start(port=self.port)
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE, 10.0),
                        "session did not reconnect after the server came back")

        await session.stop()


class PoolTest(unittest.TestCase):
    """The pool runs its own loop in a thread — exercise it from sync code."""

    def test_pool_connects_many_devices(self) -> None:
        servers: list[FakeVncServer] = []
        loop = asyncio.new_event_loop()

        async def boot():
            for _ in range(12):
                server = FakeVncServer()
                await server.start()
                servers.append(server)

        loop.run_until_complete(boot())

        # Keep the fake servers alive on their own loop thread.
        import threading
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        frames: list[Frame] = []
        pool = DevicePool(fast_settings(), on_frame=frames.append,
                          on_status=lambda k, s, d: None)
        pool.start()
        specs = [DeviceSpec(host="127.0.0.1", port=s.port) for s in servers]
        pool.set_devices(specs)
        pool.set_tiers({specs[0].key: Tier.GRID})

        # Rộng tay: khi chạy cùng cả bộ test, máy bận hơn nhiều so với lúc chạy
        # riêng. Đây là test về "có kết nối đủ không", không phải test tốc độ.
        #
        # Phải đếm số máy **khác nhau** đã gửi hình, không phải tổng số khung:
        # một máy gửi hai khung là điều kiện tổng bị thoả sớm trong khi vẫn còn
        # máy chưa gửi gì.
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if (pool.stats()["online"] == len(specs)
                    and len({f.key for f in frames}) == len(specs)):
                break
            time.sleep(0.05)

        stats = pool.stats()
        keys_with_frames = {f.key for f in frames}
        async def shutdown_servers():
            await asyncio.gather(*(s.stop() for s in servers))

        pool.stop()
        asyncio.run_coroutine_threadsafe(shutdown_servers(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)

        self.assertEqual(stats["online"], len(specs), f"stats: {stats}")
        self.assertEqual(len(keys_with_frames), len(specs),
                         "not every device produced its initial thumbnail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
