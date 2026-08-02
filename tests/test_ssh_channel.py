"""Kênh SSH — chạy với server SSH thật, dựng trong tiến trình."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.ssh_channel import (                       # noqa: E402
    CommandResult, SshAuthError, SshChannel, SshError, SshUnavailable,
)
from tests.fake_ssh import FakeSshServer                   # noqa: E402


class SshChannelTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeSshServer()
        port = await self.server.start()
        self.channel = SshChannel("127.0.0.1", port, self.server.username,
                                  self.server.password, timeout=10)

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_runs_a_command_and_returns_output(self) -> None:
        self.server.expect("uname -a", stdout="Darwin iPhone 22.6.0\n")

        result = await self.channel.run("uname -a")

        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Darwin", result.stdout)
        self.assertEqual(self.server.commands, ["uname -a"])

    async def test_failing_command_keeps_its_exit_code(self) -> None:
        self.server.expect("ls /khong-co", code=1, stderr="No such file\n")

        result = await self.channel.run("ls /khong-co")

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("No such file", result.output)

    async def test_check_raises_on_failure_but_not_on_success(self) -> None:
        self.server.expect("true", code=0)
        self.server.expect("false", code=1, stderr="nope")

        (await self.channel.run("true")).check()
        with self.assertRaises(SshError):
            (await self.channel.run("false")).check()

    async def test_output_prefers_stderr_when_the_command_failed(self) -> None:
        self.server.expect("x", code=2, stdout="mot chut stdout", stderr="ly do that")
        self.assertEqual((await self.channel.run("x")).output, "ly do that")

    async def test_many_commands_share_one_connection(self) -> None:
        for name in ("a", "b", "c"):
            self.server.expect(name, stdout=name)

        results = await self.channel.run_all(["a", "b", "c"])

        self.assertEqual([r.stdout for r in results], ["a", "b", "c"])
        self.assertEqual(self.server.commands, ["a", "b", "c"])

    async def test_wrong_password_is_reported_as_auth_not_unavailable(self) -> None:
        """Phân biệt được 'sai mật khẩu' với 'máy chưa jailbreak'."""

        bad = SshChannel("127.0.0.1", self.server.port, "root", "sai-mat-khau",
                         timeout=10)
        with self.assertRaises(SshAuthError) as ctx:
            await bad.run("true")
        self.assertIn("sai tài khoản", str(ctx.exception))

    async def test_closed_port_says_the_device_is_probably_not_jailbroken(self) -> None:
        dead = SshChannel("127.0.0.1", 1, "root", "alpine", timeout=3)
        with self.assertRaises(SshUnavailable) as ctx:
            await dead.run("true")
        self.assertIn("chưa jailbreak", str(ctx.exception))

    async def test_is_available_tells_the_two_apart(self) -> None:
        self.server.expect("true", code=0)
        self.assertTrue(await self.channel.is_available())

        dead = SshChannel("127.0.0.1", 1, "root", "alpine", timeout=3)
        self.assertFalse(await dead.is_available())

    async def test_upload_then_download_round_trips(self) -> None:
        payload = bytes(range(256)) * 40        # 10 KB nhị phân
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "gui.bin"
            src.write_bytes(payload)
            remote = str(Path(tmp) / "tren-may.bin").replace("\\", "/")
            back = Path(tmp) / "lay-ve.bin"

            await self.channel.upload(src, remote)
            await self.channel.download(remote, back)

            self.assertEqual(back.read_bytes(), payload)

    async def test_download_of_a_missing_file_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SshError):
                await self.channel.download("/khong/co/file", Path(tmp) / "x")


class CommandResultTest(unittest.TestCase):
    def test_ok_is_exit_code_zero(self) -> None:
        self.assertTrue(CommandResult("x", 0, "", "").ok)
        self.assertFalse(CommandResult("x", 1, "", "").ok)

    def test_output_is_trimmed(self) -> None:
        self.assertEqual(CommandResult("x", 0, "  ket qua \n", "").output, "ket qua")


if __name__ == "__main__":
    unittest.main(verbosity=2)
