"""Kênh điều khiển: liệt kê / mở / đóng app theo bundle id."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios import script                                   # noqa: E402
from controlios.control_channel import (                        # noqa: E402
    AppInfo, ControlChannel, ControlError, NotPatchedError, UnauthorizedError, probe,
)
from tests.fake_control import FakeControlServer                # noqa: E402


class ControlChannelTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeControlServer()
        port = await self.server.start()
        self.channel = ControlChannel("127.0.0.1", port, self.server.token, timeout=3)

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_lists_apps_with_all_fields(self) -> None:
        apps = await self.channel.list_apps()
        by_id = {a.bundle_id: a for a in apps}

        self.assertIn("com.golike.app", by_id)
        golike = by_id["com.golike.app"]
        self.assertEqual(golike.name, "GoLike")
        self.assertEqual(golike.kind, "User")
        self.assertEqual(golike.version, "2.1.0")
        self.assertTrue(golike.is_user_app)

    async def test_user_only_hides_system_apps(self) -> None:
        apps = await self.channel.list_apps(user_only=True)
        self.assertTrue(apps)
        self.assertFalse([a for a in apps if a.kind == "System"])
        self.assertNotIn("com.apple.Preferences", [a.bundle_id for a in apps])

    async def test_apps_sorted_user_first_then_by_name(self) -> None:
        apps = await self.channel.list_apps()
        kinds = [a.is_user_app for a in apps]
        self.assertEqual(kinds, sorted(kinds, reverse=True), "app người dùng phải lên trước")
        user_names = [a.display_name for a in apps if a.is_user_app]
        self.assertEqual(user_names, sorted(user_names, key=str.lower))

    async def test_launch_marks_app_running(self) -> None:
        await self.channel.launch("com.honeygain.app")
        self.assertEqual(self.server.launched, ["com.honeygain.app"])
        self.assertIn("com.honeygain.app", self.server.running)

    async def test_launch_unknown_bundle_raises(self) -> None:
        with self.assertRaises(ControlError):
            await self.channel.launch("com.khong.ton.tai")

    async def test_terminate_returns_true_then_false(self) -> None:
        self.assertTrue(await self.channel.terminate("com.golike.app"))
        self.assertEqual(self.server.terminated, ["com.golike.app"])
        # Lần hai: app đã tắt rồi -> False, không phải lỗi.
        self.assertFalse(await self.channel.terminate("com.golike.app"))

    async def test_wrong_token_is_reported_clearly(self) -> None:
        bad = ControlChannel("127.0.0.1", self.server.port, "SaiToken", timeout=3)
        with self.assertRaises(UnauthorizedError) as ctx:
            await bad.list_apps()
        self.assertIn("TVNC_CTL_TOKEN", str(ctx.exception))
        self.assertEqual(self.server.unauthorized, 1)

    async def test_unpatched_server_says_so(self) -> None:
        self.server.unpatched = True
        with self.assertRaises(NotPatchedError) as ctx:
            await self.channel.list_apps()
        self.assertIn("chưa cài bản đã vá", str(ctx.exception))

    async def test_unreachable_host_message_is_actionable(self) -> None:
        dead = ControlChannel("127.0.0.1", 1, "x", timeout=1)
        with self.assertRaises(ControlError) as ctx:
            await dead.list_apps()
        self.assertIn("không phản hồi", str(ctx.exception))

    async def test_malformed_rows_are_skipped_not_fatal(self) -> None:
        self.server.apps = {"com.ok.app": ("Ổn", "User", "1.0")}

        original = self.server._respond

        def respond(cmd: str) -> bytes:
            if cmd == "apps":
                return b"dong-rac-khong-co-tab\ncom.ok.app\tOn\tUser\t1.0\n\n"
            return original(cmd)

        self.server._respond = respond
        apps = await self.channel.list_apps()
        self.assertEqual([a.bundle_id for a in apps], ["com.ok.app"])

    async def test_short_rows_are_padded(self) -> None:
        original = self.server._respond
        self.server._respond = lambda cmd: (
            b"com.x.y\tTen\n" if cmd == "apps" else original(cmd)
        )
        apps = await self.channel.list_apps()
        self.assertEqual(apps[0], AppInfo("com.x.y", "Ten", "", ""))

    async def test_probe_returns_none_when_usable(self) -> None:
        self.assertIsNone(await probe("127.0.0.1", self.server.port, self.server.token))

    async def test_probe_explains_when_not_usable(self) -> None:
        reason = await probe("127.0.0.1", self.server.port, "SaiToken")
        self.assertIsNotNone(reason)
        self.assertIn("token", reason.lower())

    async def test_count_uses_the_stock_command(self) -> None:
        self.assertEqual(await self.channel.client_count(), 1)


class ScriptAppCommandTest(unittest.TestCase):
    def test_parses_bundle_id_commands(self) -> None:
        steps = script.parse("launchapp com.zing.zalo\nkillapp com.golike.app")
        self.assertEqual([s.op for s in steps], ["launchapp", "killapp"])
        self.assertEqual(steps[0].args, ("com.zing.zalo",))

    def test_rejects_display_name_and_points_at_openapp(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("launchapp Zalo")
        self.assertIn("openapp Zalo", str(ctx.exception))

    def test_rejects_bundle_with_spaces(self) -> None:
        with self.assertRaises(script.ScriptError):
            script.parse("launchapp com.zing zalo")

    def test_missing_argument_is_reported(self) -> None:
        with self.assertRaises(script.ScriptError):
            script.parse("killapp")

    def test_describe_says_it_uses_the_control_channel(self) -> None:
        lines = script.describe(script.parse("launchapp com.zing.zalo"))
        self.assertEqual(lines, ["mở app com.zing.zalo (qua kênh điều khiển)"])

    def test_counted_as_one_step(self) -> None:
        self.assertEqual(script.count_steps(script.parse("launchapp com.a.b")), 1)


class ScriptRunnerTest(unittest.IsolatedAsyncioTestCase):
    """Kịch bản gọi kênh điều khiển, và báo lỗi rõ khi chưa cấu hình."""

    async def asyncSetUp(self) -> None:
        self.server = FakeControlServer()
        port = await self.server.start()
        self.channel = ControlChannel("127.0.0.1", port, self.server.token, timeout=3)

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    class _FakeSession:
        class _Spec:
            key = "10.0.0.1:5901"
        spec = _Spec()

        class _Video:
            width = 375
            height = 812

        class _Client:
            video = None

        def __init__(self):
            self._client = self._Client()
            self._client.video = self._Video()

    async def test_runner_drives_the_control_channel(self) -> None:
        steps = script.parse("launchapp com.honeygain.app\nkillapp com.golike.app")
        await script.run_on_session(
            self._FakeSession(), steps, lambda k, m: None, control=self.channel
        )
        self.assertEqual(self.server.launched, ["com.honeygain.app"])
        self.assertEqual(self.server.terminated, ["com.golike.app"])

    async def test_runner_explains_when_channel_missing(self) -> None:
        steps = script.parse("launchapp com.honeygain.app")
        with self.assertRaises(ConnectionError) as ctx:
            await script.run_on_session(
                self._FakeSession(), steps, lambda k, m: None, control=None
            )
        self.assertIn("control_token", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
