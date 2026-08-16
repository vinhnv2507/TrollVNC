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

    async def test_get_color_returns_hex(self) -> None:
        self.server.color_hex = "4285F4"
        hexv = await self.channel.get_color(0.5, 0.2)
        self.assertEqual(hexv, "4285F4")

    async def test_wake_if_locked_only_presses_home_when_locked(self) -> None:
        self.assertFalse(await self.channel.wake_if_locked())
        self.assertEqual(self.server.home_count, 0)
        self.server.locked = True
        self.assertTrue(await self.channel.wake_if_locked())
        self.assertEqual(self.server.home_count, 1)

    async def test_measure_app_traffic_parses_process_bytes(self) -> None:
        bundle = "com.golike.app"
        self.server.running.add(bundle)
        result = await self.channel.measure_app_traffic(bundle, 8)
        self.assertEqual(result.bytes_in, 4096)
        self.assertEqual(result.bytes_out, 2048)
        self.assertEqual(result.pid, 1234)
        self.assertEqual(result.bytes_per_second, 768)

    async def test_measure_app_traffic_requires_running_app(self) -> None:
        with self.assertRaisesRegex(ControlError, "chưa chạy"):
            await self.channel.measure_app_traffic("com.example.notrunning", 8)

    async def test_download_snapshot_tree_without_ssh(self) -> None:
        import tempfile
        root = "/var/mobile/controlios-snap/com.golike.app/backup"
        self.server.received[root + "/Documents/a.txt"] = b"xin chao"
        self.server.received[root + "/Library/prefs.plist"] = b"plist"
        with tempfile.TemporaryDirectory() as folder:
            total = await self.channel.download_tree(root, Path(folder) / "backup")
            self.assertEqual(total, 13)
            self.assertEqual((Path(folder) / "backup/Documents/a.txt").read_bytes(),
                             b"xin chao")
            self.assertEqual((Path(folder) / "backup/Library/prefs.plist").read_bytes(),
                             b"plist")

    async def test_download_snapshot_encodes_ios_names_invalid_on_windows(self) -> None:
        import tempfile
        root = "/var/mobile/controlios-snap/com.golike.app/badnames"
        self.server.received[root + "/Library/cache:one?.db"] = b"data"
        self.server.received[root + "/Documents/CON"] = b"reserved"
        self.server.received[root + "/Documents/trailing."] = b"dot"
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "backup"
            total = await self.channel.download_tree(root, target)
            self.assertEqual(total, 15)
            self.assertEqual((target / "Library/cache%3Aone%3F.db").read_bytes(), b"data")
            self.assertEqual((target / "Documents/%43ON").read_bytes(), b"reserved")
            self.assertEqual((target / "Documents/trailing%2E").read_bytes(), b"dot")

    async def test_get_color_none_when_unpatched(self) -> None:
        self.server.unpatched = True
        self.assertIsNone(await self.channel.get_color(0.5, 0.2))

    async def test_set_smoothness_sends_three_commands(self) -> None:
        # Không ném lỗi và gửi đủ 3 lệnh (lệnh cuối lưu lại ở fake).
        await self.channel.set_smoothness(1, 0.008, False)
        self.assertEqual(self.server.smoothness, "setorient off")

    async def test_set_smoothness_tolerates_unpatched(self) -> None:
        self.server.unpatched = True
        await self.channel.set_smoothness(1, 0.008, False)  # không được ném

    async def test_keeper_status_reads_both_flags(self) -> None:
        self.server.keeperd_running = True
        self.server.keeper_app_running = True
        self.assertEqual(await self.channel.get_keeper_status(), (True, True))

    async def test_keeper_status_reports_dead_keeperd(self) -> None:
        self.server.keeperd_running = False
        self.server.keeper_app_running = False
        self.assertEqual(await self.channel.get_keeper_status(), (False, False))

    async def test_ensure_keeper_starts_when_dead(self) -> None:
        self.server.keeperd_running = False
        started, note = await self.channel.ensure_keeper()
        self.assertTrue(started)
        self.assertEqual(self.server.keeper_starts, 1)
        self.assertTrue(self.server.keeperd_running)
        self.assertIn("keeperd", note)

    async def test_ensure_keeper_leaves_live_keeperd_alone(self) -> None:
        # Đang sống thì KHÔNG được gọi start — tránh spawn trùng cho ~250 máy.
        self.server.keeperd_running = True
        started, _note = await self.channel.ensure_keeper()
        self.assertFalse(started)
        self.assertEqual(self.server.keeper_starts, 0)

    async def test_ensure_keeper_rejects_spawn_without_daemon(self) -> None:
        self.server.keeperd_running = False
        self.server.keeper_start_noop = True
        with self.assertRaisesRegex(ControlError, "chưa hoạt động"):
            await self.channel.ensure_keeper()

    async def test_keeper_status_raises_when_unpatched(self) -> None:
        self.server.unpatched = True
        with self.assertRaises(ControlError):
            await self.channel.get_keeper_status()

    async def test_reboot_reaches_device(self) -> None:
        await self.channel.reboot()
        self.assertEqual(self.server.reboot_count, 1)

    async def test_shutdown_reaches_device(self) -> None:
        await self.channel.shutdown()
        self.assertEqual(self.server.shutdown_count, 1)

    async def test_push_prelude_sends_base64(self) -> None:
        import base64
        await self.channel.push_prelude("function foo(){}")
        raw = base64.b64decode(self.server.prelude)
        self.assertEqual(raw.decode(), "function foo(){}")

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

    async def test_clipboard_round_trips_utf8_with_accents(self) -> None:
        payload = "Xin chào bạn — bình luận số 1 😀 (emoji cũng qua được)"
        written = await self.channel.set_clipboard(payload)
        self.assertEqual(written, len(payload.encode("utf-8")))
        self.assertEqual(self.server.clipboard, payload)
        self.assertEqual(await self.channel.get_clipboard(), payload)

    async def test_clipboard_preserves_newlines(self) -> None:
        payload = "dòng một\ndòng hai\n"
        await self.channel.set_clipboard(payload)
        self.assertEqual(await self.channel.get_clipboard(), payload)

    async def test_clipboard_fails_clearly_on_unpatched(self) -> None:
        self.server.unpatched = True
        with self.assertRaises(NotPatchedError):
            await self.channel.set_clipboard("gì đó")

    async def test_save_photo_needs_the_file_on_device(self) -> None:
        with self.assertRaises(ControlError):
            await self.channel.save_photo("/var/mobile/Media/khong-co.png")

    async def test_respring_reaches_the_device(self) -> None:
        await self.channel.respring()
        self.assertEqual(self.server.respring_count, 1)

    def test_loopback_omits_auth_prefix(self) -> None:
        # Qua USB (loopback trên máy) server không cắt tiền tố auth -> phải bỏ nó.
        usb = ControlChannel("127.0.0.1", 6002, "tok", loopback=True)
        self.assertEqual(usb._auth_prefix(), "")
        wifi = ControlChannel("172.30.3.5", 46752, "tok")
        self.assertEqual(wifi._auth_prefix(), "auth tok ")

    async def test_set_scale_applies_and_validates(self) -> None:
        await self.channel.set_scale(0.5)
        self.assertAlmostEqual(self.server.scale, 0.5)
        with self.assertRaises(ValueError):
            await self.channel.set_scale(1.5)      # ngoài (0,1]
        with self.assertRaises(ValueError):
            await self.channel.set_scale(0.0)

    async def test_wipe_app_clears_data(self) -> None:
        await self.channel.wipe_app("com.golike.app")
        self.assertEqual(self.server.wiped, ["com.golike.app"])

    async def test_wipe_unknown_bundle_raises(self) -> None:
        with self.assertRaises(ControlError):
            await self.channel.wipe_app("com.khong.co")

    async def test_snapshot_named_then_restore_round_trips(self) -> None:
        saved = await self.channel.snapshot_app("com.golike.app", "sach")
        self.assertEqual(saved, "sach")
        self.assertIn("sach", self.server.snapshots.get("com.golike.app", {}))
        await self.channel.restore_app("com.golike.app", "sach")
        self.assertEqual(self.server.restored, [("com.golike.app", "sach")])

    async def test_snapshot_auto_names_when_blank(self) -> None:
        saved = await self.channel.snapshot_app("com.golike.app")
        self.assertTrue(saved, "phải trả về tên tự sinh")
        self.assertIn(saved, self.server.snapshots.get("com.golike.app", {}))

    async def test_list_snapshots_newest_first(self) -> None:
        await self.channel.snapshot_app("com.golike.app", "mot")
        await self.channel.snapshot_app("com.golike.app", "hai")
        snaps = await self.channel.list_snapshots("com.golike.app")
        self.assertEqual({s.name for s in snaps}, {"mot", "hai"})
        # epoch tăng dần theo thứ tự lưu -> "hai" mới hơn, phải đứng trước.
        self.assertEqual(snaps[0].name, "hai")

    async def test_delete_snapshot_removes_it(self) -> None:
        await self.channel.snapshot_app("com.golike.app", "tam")
        await self.channel.delete_snapshot("com.golike.app", "tam")
        self.assertNotIn("tam", self.server.snapshots.get("com.golike.app", {}))
        snaps = await self.channel.list_snapshots("com.golike.app")
        self.assertEqual(snaps, [])

    async def test_clear_snapshots_removes_all(self) -> None:
        await self.channel.snapshot_app("com.golike.app", "a")
        await self.channel.snapshot_app("com.golike.app", "b")
        await self.channel.clear_snapshots("com.golike.app")
        self.assertEqual(await self.channel.list_snapshots("com.golike.app"), [])

    async def test_clear_snapshots_ok_when_none(self) -> None:
        # Không có bản nào vẫn trả về OK (không lỗi).
        await self.channel.clear_snapshots("com.honeygain.app")

    async def test_restore_missing_name_is_reported(self) -> None:
        with self.assertRaises(ControlError) as ctx:
            await self.channel.restore_app("com.honeygain.app", "khong-co")
        self.assertIn("snapshot", str(ctx.exception).lower())

    async def test_reset_commands_fail_clearly_on_unpatched(self) -> None:
        self.server.unpatched = True
        with self.assertRaises(NotPatchedError):
            await self.channel.wipe_app("com.golike.app")

    async def test_push_photo_uploads_then_imports(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + b"gia-lam-anh" * 100)
            local = Path(handle.name)
        try:
            await self.channel.push_photo(local, remote_dir="/var/mobile/Media/ci")
            remote = "/var/mobile/Media/ci/" + local.name
            self.assertIn(remote, self.server.received)
            self.assertEqual(self.server.saved_photos, [remote])
        finally:
            local.unlink(missing_ok=True)


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

    def test_parses_restart_and_url_commands(self) -> None:
        steps = script.parse(
            "restartapp com.zing.zalo 2\n"
            "openurl https://example.com/path?q=1\n"
            "openurlin com.zing.zalo zalo://home"
        )
        self.assertEqual([s.op for s in steps], ["restartapp", "openurl", "openurlin"])
        self.assertEqual(steps[0].args, ("com.zing.zalo", 2.0))

    def test_retry_block_is_counted_at_worst_case(self) -> None:
        steps = script.parse("retry 3 0.5\n    launchapp com.zing.zalo")
        self.assertEqual(steps[0].args, (3, 0.5))
        self.assertEqual(script.count_steps(steps), 3)

    def test_retry_requires_an_indented_body(self) -> None:
        with self.assertRaisesRegex(script.ScriptError, "retry phải có khối"):
            script.parse("retry 3")

    def test_parses_clipboard_and_savephoto(self) -> None:
        steps = script.parse(
            "clipboard Xin chào bạn\nsavephoto /var/mobile/Media/ci/anh.png"
        )
        self.assertEqual([s.op for s in steps], ["clipboard", "savephoto"])
        self.assertEqual(steps[0].args, ("Xin chào bạn",))
        self.assertEqual(steps[1].args, ("/var/mobile/Media/ci/anh.png",))

    def test_savephoto_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(script.ScriptError, "đường dẫn tuyệt đối"):
            script.parse("savephoto anh.png")

    def test_parses_data_reset_commands(self) -> None:
        steps = script.parse(
            "wipeapp com.zing.zalo\n"
            "snapshot com.zing.zalo\n"
            "snapshot com.zing.zalo sach\n"
            "restore com.zing.zalo sach"
        )
        self.assertEqual([s.op for s in steps],
                         ["wipeapp", "snapshot", "snapshot", "restore"])
        self.assertEqual(steps[0].args, ("com.zing.zalo",))
        self.assertEqual(steps[1].args, ("com.zing.zalo", ""))       # tên tự sinh
        self.assertEqual(steps[2].args, ("com.zing.zalo", "sach"))
        self.assertEqual(steps[3].args, ("com.zing.zalo", "sach"))

    def test_restore_requires_a_snapshot_name(self) -> None:
        with self.assertRaisesRegex(script.ScriptError, "tên snapshot"):
            script.parse("restore com.zing.zalo")

    def test_data_reset_rejects_display_name(self) -> None:
        with self.assertRaises(script.ScriptError):
            script.parse("wipeapp Zalo")


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

    async def test_runner_sets_clipboard_via_control_channel(self) -> None:
        steps = script.parse("clipboard Bình luận đủ dấu")
        await script.run_on_session(
            self._FakeSession(), steps, lambda k, m: None, control=self.channel
        )
        self.assertEqual(self.server.clipboard, "Bình luận đủ dấu")

    async def test_runner_clipboard_needs_channel(self) -> None:
        steps = script.parse("clipboard gì đó")
        with self.assertRaises(ConnectionError):
            await script.run_on_session(
                self._FakeSession(), steps, lambda k, m: None, control=None
            )

    async def test_runner_restarts_app_and_opens_urls(self) -> None:
        self.server.running.add("com.honeygain.app")
        steps = script.parse(
            "restartapp com.honeygain.app 0\n"
            "openurl https://example.com/start\n"
            "openurlin com.honeygain.app honeygain://dashboard"
        )
        await script.run_on_session(
            self._FakeSession(), steps, lambda k, m: None, control=self.channel
        )
        self.assertEqual(self.server.terminated, ["com.honeygain.app"])
        self.assertEqual(self.server.launched, ["com.honeygain.app"])
        self.assertEqual(self.server.opened_urls, ["https://example.com/start"])
        self.assertEqual(
            self.server.opened_in,
            [("com.honeygain.app", "honeygain://dashboard")],
        )

    async def test_runner_wipes_and_restores_via_control_channel(self) -> None:
        self.server.running.add("com.honeygain.app")
        steps = script.parse(
            "snapshot com.honeygain.app sach\n"
            "wipeapp com.honeygain.app\n"
            "restore com.honeygain.app sach"
        )
        await script.run_on_session(
            self._FakeSession(), steps, lambda k, m: None, control=self.channel
        )
        # Mỗi lệnh đóng app trước rồi mới thao tác dữ liệu (server chỉ ghi lần
        # đóng đầu, các lần sau app vốn đã tắt).
        self.assertIn("sach", self.server.snapshots.get("com.honeygain.app", {}))
        self.assertEqual(self.server.wiped, ["com.honeygain.app"])
        self.assertEqual(self.server.restored, [("com.honeygain.app", "sach")])
        self.assertIn("com.honeygain.app", self.server.terminated)

    async def test_runner_data_reset_needs_channel(self) -> None:
        steps = script.parse("wipeapp com.honeygain.app")
        with self.assertRaises(ConnectionError):
            await script.run_on_session(
                self._FakeSession(), steps, lambda k, m: None, control=None
            )

    async def test_retry_is_independent_per_session(self) -> None:
        class FlakyControl:
            attempts = 0

            async def launch(self, _bundle):
                self.attempts += 1
                if self.attempts < 3:
                    raise ConnectionError("mạng chập chờn")

        control = FlakyControl()
        events = []
        steps = script.parse("retry 3 0\n    launchapp com.honeygain.app")
        await script.run_on_session(
            self._FakeSession(), steps, lambda k, m: events.append(m), control=control
        )
        self.assertEqual(control.attempts, 3)
        self.assertEqual(len([event for event in events if "thử lại" in event]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
