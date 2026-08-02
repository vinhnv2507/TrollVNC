"""Bật SSH bằng khoá qua control socket — thoát bế tắc mật khẩu Dopamine."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncssh                                             # noqa: E402

from controlios.control_channel import ControlChannel      # noqa: E402
from controlios.ssh_channel import SshChannel              # noqa: E402
from tests.fake_control import FakeControlServer           # noqa: E402
from tests.fake_ssh import FakeSshServer                   # noqa: E402


def a_public_key() -> tuple[str, str]:
    """Trả về (khoá riêng dạng OpenSSH, khoá công khai dạng OpenSSH)."""
    key = asyncssh.generate_private_key("ssh-ed25519")
    return (key.export_private_key().decode(), key.export_public_key().decode().strip())


class InstallKeyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeControlServer()
        port = await self.server.start()
        self.channel = ControlChannel("127.0.0.1", port, self.server.token, timeout=5)

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_key_is_written_to_roots_authorized_keys(self) -> None:
        _priv, pub = a_public_key()

        remote = await self.channel.install_ssh_key(pub, user="root")

        self.assertEqual(remote, "/var/root/.ssh/authorized_keys")
        written = self.server.received["/var/root/.ssh/authorized_keys"].decode()
        self.assertEqual(written.strip(), pub)
        self.assertTrue(written.endswith("\n"), "authorized_keys phải kết thúc bằng newline")

    async def test_mobile_user_targets_mobile_home(self) -> None:
        _priv, pub = a_public_key()
        remote = await self.channel.install_ssh_key(pub, user="mobile")
        self.assertEqual(remote, "/var/mobile/.ssh/authorized_keys")


class KeyAuthEndToEndTest(unittest.IsolatedAsyncioTestCase):
    """Khoá cài xong thì đăng nhập SSH được mà không cần mật khẩu."""

    async def asyncSetUp(self) -> None:
        self.priv, self.pub = a_public_key()
        self.key_file = Path(__file__).parent / "_test_key"
        self.key_file.write_text(self.priv)

        self.ssh = FakeSshServer(password="khong-dung-mat-khau",
                                 authorized_keys=[self.pub])
        self.port = await self.ssh.start()

    async def asyncTearDown(self) -> None:
        await self.ssh.stop()
        self.key_file.unlink(missing_ok=True)

    async def test_login_with_key_no_password(self) -> None:
        self.ssh.expect("id", stdout="uid=0(root)\n")

        channel = SshChannel("127.0.0.1", self.port, "root",
                             key_path=str(self.key_file), timeout=10)
        result = await channel.run("id")

        self.assertTrue(result.ok)
        self.assertIn("uid=0", result.stdout)

    async def test_a_wrong_key_is_refused(self) -> None:
        _other_priv, _ = a_public_key()
        wrong = Path(__file__).parent / "_wrong_key"
        wrong.write_text(_other_priv)
        try:
            channel = SshChannel("127.0.0.1", self.port, "root",
                                 key_path=str(wrong), timeout=10)
            with self.assertRaises(Exception):
                await channel.run("id")
        finally:
            wrong.unlink(missing_ok=True)


class FullSetupFlowTest(unittest.IsolatedAsyncioTestCase):
    """Cả quy trình: cài khoá qua control socket rồi SSH vào bằng khoá đó."""

    async def asyncSetUp(self) -> None:
        self.priv, self.pub = a_public_key()
        self.control = FakeControlServer()
        cport = await self.control.start()
        self.control_channel = ControlChannel("127.0.0.1", cport,
                                              self.control.token, timeout=5)

        # SSH server ban đầu KHÔNG có khoá nào — giống máy chưa cấu hình.
        self.ssh = FakeSshServer(authorized_keys=[])
        self.sport = await self.ssh.start()

    async def asyncTearDown(self) -> None:
        await self.control.stop()
        await self.ssh.stop()

    async def test_before_setup_key_login_fails(self) -> None:
        key_file = Path(__file__).parent / "_flow_key"
        key_file.write_text(self.priv)
        try:
            channel = SshChannel("127.0.0.1", self.sport, "root",
                                 key_path=str(key_file), timeout=8)
            self.assertFalse(await channel.is_available())
        finally:
            key_file.unlink(missing_ok=True)

    async def test_after_installing_the_key_login_works(self) -> None:
        # Cài khoá qua control socket (đóng vai trò trollvncserver chạy root).
        await self.control_channel.install_ssh_key(self.pub, user="root")
        installed = self.control.received["/var/root/.ssh/authorized_keys"].decode()

        # Máy giờ chấp nhận khoá đó.
        self.ssh.authorized_keys.append(installed.strip())
        self.ssh.expect("id", stdout="uid=0(root)\n")

        key_file = Path(__file__).parent / "_flow_key2"
        key_file.write_text(self.priv)
        try:
            channel = SshChannel("127.0.0.1", self.sport, "root",
                                 key_path=str(key_file), timeout=10)
            self.assertTrue(await channel.is_available())
        finally:
            key_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
