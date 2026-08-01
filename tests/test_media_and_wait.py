"""Độ sáng / âm lượng, và lệnh chờ ngẫu nhiên."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios import script                                   # noqa: E402
from controlios.config import DeviceSpec, Settings              # noqa: E402
from controlios.vnc.session import (                            # noqa: E402
    BRIGHTNESS_STEPS, MEDIA_KEYSYMS, State, Tier, VncSession,
)
from tests.fake_vnc import FakeVncServer                        # noqa: E402


class MediaKeyOnDeviceTest(unittest.IsolatedAsyncioTestCase):
    """Keysym XF86 phải tới server đúng mã — asyncvnc không có tên cho chúng."""

    async def asyncSetUp(self) -> None:
        self.server = FakeVncServer()
        port = await self.server.start()
        self.session = VncSession(
            DeviceSpec(host="127.0.0.1", port=port),
            Settings(grid_fps=20.0, stall_timeout=5.0, reconnect_delay=0.2),
            asyncio.Semaphore(4),
            on_frame=lambda f: None, on_status=lambda k, s, d: None,
        )
        self.session.set_tier(Tier.GRID)
        self.session.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self.session.state is not State.ONLINE:
            await asyncio.sleep(0.05)
        self.assertIs(self.session.state, State.ONLINE)
        self.server.key_events.clear()

    async def asyncTearDown(self) -> None:
        await self.session.stop()
        await self.server.stop()

    async def _wait_keys(self, count: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.server.key_events) >= count:
                return True
            await asyncio.sleep(0.05)
        return False

    async def test_brightness_down_sends_the_xf86_keysym(self) -> None:
        self.session.media_key("brightness_down")
        self.assertTrue(await self._wait_keys(2))

        self.assertEqual(self.server.key_events[0], (1, 0x1008FF03))
        self.assertEqual(self.server.key_events[1], (0, 0x1008FF03))

    async def test_brightness_up_is_a_different_keysym(self) -> None:
        self.session.media_key("brightness_up")
        self.assertTrue(await self._wait_keys(2))
        self.assertEqual(self.server.key_events[0], (1, 0x1008FF02))

    async def test_repeat_sends_that_many_press_release_pairs(self) -> None:
        self.session.media_key("brightness_down", BRIGHTNESS_STEPS)
        self.assertTrue(await self._wait_keys(BRIGHTNESS_STEPS * 2))

        downs = [e for e in self.server.key_events if e[0] == 1]
        ups = [e for e in self.server.key_events if e[0] == 0]
        self.assertEqual(len(downs), BRIGHTNESS_STEPS)
        self.assertEqual(len(ups), BRIGHTNESS_STEPS)

    async def test_volume_and_mute_map_to_their_own_keysyms(self) -> None:
        for name, expected in [("volume_up", 0x1008FF13), ("volume_down", 0x1008FF11),
                               ("mute", 0x1008FF12)]:
            self.server.key_events.clear()
            self.session.media_key(name)
            self.assertTrue(await self._wait_keys(2))
            self.assertEqual(self.server.key_events[0], (1, expected), name)

    async def test_unknown_name_is_ignored_not_fatal(self) -> None:
        self.session.media_key("khong-co-phim-nay")
        await asyncio.sleep(0.3)
        self.assertEqual(self.server.key_events, [])
        self.assertIs(self.session.state, State.ONLINE)

    async def test_every_named_keysym_is_in_the_xf86_range(self) -> None:
        for name, keysym in MEDIA_KEYSYMS.items():
            self.assertEqual(keysym & 0xFFFF0000, 0x10080000, name)


class WaitRangeTest(unittest.TestCase):
    def test_plain_number_is_a_fixed_wait(self) -> None:
        steps = script.parse("wait 2.5")
        self.assertEqual(steps[0].args, (2.5, 2.5))

    def test_range_is_parsed(self) -> None:
        steps = script.parse("wait 5-10")
        self.assertEqual(steps[0].args, (5.0, 10.0))

    def test_reversed_range_is_rejected(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("wait 10-5")
        self.assertIn("ngược", str(ctx.exception))

    def test_negative_is_rejected(self) -> None:
        with self.assertRaises(script.ScriptError):
            script.parse("wait -3")

    def test_describe_distinguishes_fixed_from_random(self) -> None:
        self.assertEqual(script.describe(script.parse("wait 3")), ["chờ 3.0s"])
        self.assertEqual(script.describe(script.parse("wait 5-10")),
                         ["chờ ngẫu nhiên 5.0–10.0s"])


class MediaScriptTest(unittest.TestCase):
    def test_brightness_min_uses_every_step(self) -> None:
        steps = script.parse("brightness min")
        self.assertEqual(steps[0].args, ("brightness_down", BRIGHTNESS_STEPS, "min"))

    def test_brightness_max_goes_up(self) -> None:
        self.assertEqual(script.parse("brightness max")[0].args[0], "brightness_up")

    def test_explicit_step_count(self) -> None:
        self.assertEqual(script.parse("brightness down 4")[0].args,
                         ("brightness_down", 4, "down"))

    def test_volume_mute(self) -> None:
        self.assertEqual(script.parse("volume mute")[0].args, ("mute", 1, "mute"))

    def test_brightness_has_no_mute(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("brightness mute")
        self.assertNotIn("mute", str(ctx.exception).split("cú pháp:")[1])

    def test_min_does_not_take_a_step_count(self) -> None:
        with self.assertRaises(script.ScriptError):
            script.parse("brightness min 4")

    def test_step_count_must_be_a_positive_integer(self) -> None:
        for bad in ("brightness down 0", "brightness down -2", "brightness down x"):
            with self.subTest(source=bad):
                with self.assertRaises(script.ScriptError):
                    script.parse(bad)

    def test_describe_is_readable(self) -> None:
        self.assertEqual(script.describe(script.parse("brightness min")),
                         ["xuống thấp nhất độ sáng"])
        self.assertEqual(script.describe(script.parse("brightness down 3")),
                         ["giảm độ sáng 3 nấc"])


class RunnerTest(unittest.IsolatedAsyncioTestCase):
    class _FakeSession:
        class _Spec:
            key = "10.0.0.1:5901"
        spec = _Spec()

        class _Video:
            width = 375
            height = 812

        def __init__(self):
            self.media_calls = []
            self._client = type("C", (), {"video": self._Video()})()

        def media_key(self, name, repeat=1):
            self.media_calls.append((name, repeat))

    async def test_runner_sends_the_media_key(self) -> None:
        session = self._FakeSession()
        await script.run_on_session(
            session, script.parse("brightness min"), lambda k, m: None
        )
        self.assertEqual(session.media_calls, [("brightness_down", BRIGHTNESS_STEPS)])

    async def test_random_wait_stays_inside_the_range(self) -> None:
        session = self._FakeSession()
        slept = []

        async def fake_sleep(seconds):
            slept.append(seconds)

        with unittest.mock.patch("controlios.script.asyncio.sleep", fake_sleep):
            for _ in range(30):
                await script.run_on_session(
                    session, script.parse("wait 5-10"), lambda k, m: None
                )

        self.assertEqual(len(slept), 30)
        self.assertTrue(all(5.0 <= s <= 10.0 for s in slept), slept)
        self.assertGreater(len(set(slept)), 1, "phải thật sự ngẫu nhiên, không cố định")

    async def test_fixed_wait_is_not_randomised(self) -> None:
        session = self._FakeSession()
        slept = []

        async def fake_sleep(seconds):
            slept.append(seconds)

        with unittest.mock.patch("controlios.script.asyncio.sleep", fake_sleep):
            for _ in range(5):
                await script.run_on_session(
                    session, script.parse("wait 3"), lambda k, m: None
                )
        self.assertEqual(set(slept), {3.0})

    async def test_close_wait_reopen_is_expressible(self) -> None:
        """Đúng kịch bản người dùng cần: đóng app, chờ 5-10s, mở lại."""

        steps = script.parse(
            "killapp com.zing.zalo\n"
            "wait 5-10\n"
            "launchapp com.zing.zalo\n"
        )
        self.assertEqual([s.op for s in steps], ["killapp", "wait", "launchapp"])
        self.assertEqual(steps[1].args, (5.0, 10.0))
        self.assertEqual(script.count_steps(steps), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
