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

    async def test_request_resync_reconnects_cleanly(self) -> None:
        # Đổi scale làm framebuffer đổi cỡ -> yêu cầu nối lại. Phiên phải bắt tay
        # lại (xuất hiện CONNECTING) rồi ONLINE trở lại. Kiểm tra qua LỊCH SỬ trạng
        # thái vì reconnect rất nhanh, bắt trạng thái tức thời dễ hụt.
        session = self.make_session()
        session.set_tier(Tier.GRID)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))

        self.states.clear()
        session.request_resync()
        self.assertTrue(
            await self.wait_for(
                lambda: any(s is State.CONNECTING for _, s in self.states), 8.0),
            "resync phải làm phiên nối lại (có CONNECTING)")
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE, 10.0),
                        "resync phải nối lại được")

        await session.stop()

    async def test_mouse_down_boosts_low_live_fps(self) -> None:
        settings = fast_settings(live_fps=12.0)
        session = self.make_session(settings)
        session.set_tier(Tier.LIVE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 2))

        self.assertGreaterEqual(session._effective_fps(Tier.LIVE), 30.0)
        session.mouse_move(10, 10)
        self.assertGreaterEqual(session._effective_fps(Tier.LIVE), 30.0)
        session.mouse_down(10, 10)
        self.assertGreaterEqual(session._effective_fps(Tier.LIVE), 30.0)
        self.assertTrue(session._interact.is_set())
        await session.stop()


    async def test_live_pipelines_two_framebuffer_requests(self) -> None:
        session = self.make_session(fast_settings(live_fps=12.0))
        session.set_tier(Tier.LIVE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 1))
        self.assertGreaterEqual(
            self.server.update_requests, 2,
            "LIVE must keep a second FBUR in flight to hide RTT",
        )
        self.assertTrue(any(7 in enc for enc in self.server.encodings),
                        f"client did not ask for Tight: {self.server.encodings}")
        await session.stop()

    async def test_live_does_not_pipeline_while_dragging(self) -> None:
        session = self.make_session(fast_settings(live_fps=12.0))
        session.set_tier(Tier.LIVE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 2))

        session.mouse_down(40, 80)
        start_req = self.server.update_requests
        start_frames = len(self.frames)
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= start_frames + 8))
        new_req = self.server.update_requests - start_req
        new_frames = len(self.frames) - start_frames
        self.assertLessEqual(
            new_req, new_frames + 1,
            f"drag pipelined FBURs: requests={new_req} frames={new_frames}",
        )
        session.mouse_up(90, 80)
        await session.stop()

    async def test_live_keeps_frames_when_drag_starts_on_q1_device(self) -> None:
        """0.2.15 froze video for stall_timeout after mouse_down on Q=1.

        Extra pipelined FBURs are dropped while an encode is busy. After
        depth drops to 1 the leftover inflight is a ghost; the pacer must
        send a fresh FBUR instead of waiting 20s.
        """
        self.server.max_inflight = 1
        self.server.encode_delay = 0.05
        session = self.make_session(fast_settings(live_fps=12.0, stall_timeout=5.0))
        session.set_tier(Tier.LIVE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 2))

        session.mouse_down(40, 80)
        start_frames = len(self.frames)
        self.assertTrue(
            await self.wait_for(lambda: len(self.frames) >= start_frames + 4, timeout=2.0),
            f"video stalled after drag: got {len(self.frames) - start_frames} "
            f"frames inflight={session._inflight}",
        )
        session.mouse_up(90, 80)
        await session.stop()

    async def test_live_sends_drag_path_not_just_release(self) -> None:
        """Captcha sliders need intermediate PointerEvents while held.

        call_soon coalescing plus Qt batching used to emit down, then one
        move on mouse_up: the puzzle piece jumped and TikTok/Shopee failed.
        """
        session = self.make_session(fast_settings(live_fps=12.0))
        session.set_tier(Tier.LIVE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))
        self.server.pointer_events.clear()

        session.mouse_down(40, 80)
        for x in (50, 60, 70, 80, 90):
            session.mouse_move(x, 80)
            await asyncio.sleep(0)
        held = [(b, x, y) for b, x, y in self.server.pointer_events if b == 1]
        self.assertGreaterEqual(
            len({x for _, x, _ in held}), 4,
            f"drag path collapsed before release: {self.server.pointer_events}",
        )
        session.mouse_up(90, 80)
        self.assertTrue(await self.wait_for(
            lambda: self.server.pointer_events and self.server.pointer_events[-1][0] == 0
        ))
        await session.stop()

    async def test_live_keeps_frames_while_dragging_with_moves(self) -> None:
        self.server.max_inflight = 1
        self.server.encode_delay = 0.03
        session = self.make_session(fast_settings(live_fps=12.0, stall_timeout=5.0))
        session.set_tier(Tier.LIVE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 2))

        session.mouse_down(40, 80)
        start_frames = len(self.frames)
        for x in range(45, 95, 5):
            session.mouse_move(x, 80)
            await asyncio.sleep(0.02)
        self.assertTrue(
            await self.wait_for(lambda: len(self.frames) >= start_frames + 3, timeout=2.0),
            f"video stalled during drag moves: got {len(self.frames) - start_frames} "
            f"frames inflight={session._inflight}",
        )
        session.mouse_up(90, 80)
        await session.stop()

    async def test_idle_still_sends_one_request(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.IDLE)
        session.start()
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 1))
        requests = self.server.update_requests
        await asyncio.sleep(0.4)
        self.assertEqual(self.server.update_requests, requests)
        self.assertEqual(requests, 1)
        await session.stop()

    async def test_grid_does_not_pipeline(self) -> None:
        session = self.make_session(fast_settings(grid_fps=20.0))
        session.set_tier(Tier.GRID)
        session.start()
        self.assertTrue(await self.wait_for(lambda: len(self.frames) >= 4))
        # One in-flight at a time: requests stay within one of the frame count.
        self.assertLessEqual(self.server.update_requests, len(self.frames) + 1)
        await session.stop()



    async def test_new_grid_session_requests_a_full_first_frame(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.GRID)
        session.start()
        self.assertTrue(await self.wait_for(lambda: self.server.fb_incremental),
                        "no framebuffer request")
        self.assertFalse(self.server.fb_incremental[0],
                         "first GRID request must be a full frame")
        await session.stop()

    async def test_reconnect_while_already_grid_requests_full_frame(self) -> None:
        session = self.make_session()
        session.set_tier(Tier.GRID)
        session.start()
        self.assertTrue(await self.wait_for(lambda: session.state is State.ONLINE))
        await session.stop()
        self.server.fb_incremental.clear()
        session.start()
        self.assertTrue(await self.wait_for(lambda: self.server.fb_incremental),
                        "reconnect sent no framebuffer request")
        self.assertFalse(self.server.fb_incremental[0],
                         "reconnect at GRID must request a full frame, not incremental")
        await session.stop()

class InteractBoostTest(unittest.IsolatedAsyncioTestCase):
    def _session(self, **kw) -> VncSession:
        return VncSession(
            DeviceSpec(host="127.0.0.1", port=1),
            fast_settings(**kw),
            asyncio.Semaphore(1),
            on_frame=lambda f: None,
            on_status=lambda *a: None,
        )

    def test_effective_fps_boosts_during_pointer_activity(self) -> None:
        session = self._session(live_fps=12.0, grid_fps=5.0)
        self.assertEqual(session._effective_fps(Tier.LIVE), 30.0)
        self.assertEqual(session._effective_fps(Tier.GRID), 5.0)
        session._note_pointer_activity()
        self.assertEqual(session._effective_fps(Tier.LIVE), 30.0)
        self.assertEqual(session._effective_fps(Tier.GRID), 30.0)

    def test_pipeline_depth_drops_while_interacting(self) -> None:
        session = self._session()
        self.assertEqual(session._pipeline_depth(Tier.LIVE), 2)
        self.assertEqual(session._pipeline_depth(Tier.GRID), 1)
        session._note_pointer_activity()
        self.assertEqual(session._pipeline_depth(Tier.LIVE), 1)
        self.assertEqual(session._pipeline_depth(Tier.GRID), 1)
        self.assertTrue(session._want_low_latency())

    def test_discard_dropped_inflight_on_drag(self) -> None:
        session = self._session()
        session._inflight = 2
        session._discard_dropped_inflight(session._pipeline_depth(Tier.LIVE))
        self.assertEqual(session._inflight, 2)

        session._note_pointer_activity()
        session._inflight = 2
        session._discard_dropped_inflight(session._pipeline_depth(Tier.LIVE))
        self.assertEqual(session._inflight, 0)

        # Later ticks must keep a real in-flight encode. Zeroing every drag
        # frame floods TrollVNC Q=1 and freezes VIDEO until mouse-up.
        session._inflight = 1
        session._discard_dropped_inflight(session._pipeline_depth(Tier.LIVE))
        self.assertEqual(session._inflight, 1)

    def test_live_fps_floor_does_not_raise_grid(self) -> None:
        session = self._session(live_fps=8.0, grid_fps=1.0)
        self.assertEqual(session._effective_fps(Tier.LIVE), 30.0)
        self.assertEqual(session._effective_fps(Tier.GRID), 1.0)

    def test_boost_does_not_lower_high_live_fps(self) -> None:
        session = self._session(live_fps=40.0)
        session._note_pointer_activity()
        self.assertEqual(session._effective_fps(Tier.LIVE), 40.0)

    async def test_pacer_sleep_wakes_on_pointer_activity(self) -> None:
        session = self._session(live_fps=12.0)
        started = time.monotonic()
        task = asyncio.create_task(session._await_pace_gap(1.0))
        await asyncio.sleep(0.05)
        session._note_pointer_activity()
        await asyncio.wait_for(task, timeout=0.5)
        self.assertLess(time.monotonic() - started, 0.4)


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
