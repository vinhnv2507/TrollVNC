import asyncio
import json
import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from controlios.control_channel import ControlError
from controlios.ios_update import (inspect_package, stage_package, find_latest_package,
    UpdateServer, update_one, vnc_ready, version_key)


def write_package(path, version="4.18", bundle="com.controlios.app", helper=True):
    root = "Payload/ControlIOS.app/"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(root + "Info.plist", plistlib.dumps({
            "CFBundleIdentifier": bundle, "CFBundleShortVersionString": version,
            "CFBundleExecutable": "ControlIOS"}))
        for name in ["ControlIOS", "trollvncserver", "trollvncmanager"] + (["controliosupdater"] if helper else []):
            archive.writestr(root + name, b"synthetic executable")
    return path


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "ControlIOS.tipa"

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_version_identity_and_sha(self):
        package = inspect_package(write_package(self.path))
        self.assertEqual(package.version, "4.18")
        self.assertEqual(len(package.sha256), 64)
        self.assertEqual(package.size, self.path.stat().st_size)

    def test_refuse_other_app_and_missing_updater(self):
        for kwargs in ({"bundle": "com.other.app"}, {"helper": False}, {"version": "4.18;bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                inspect_package(write_package(self.path, **kwargs))

    def test_refuse_multiple_payload_apps_and_traversal(self):
        for name in ("Payload/Other.app/executable", "Payload/../evil", "/evil"):
            write_package(self.path)
            with zipfile.ZipFile(self.path, "a") as archive:
                archive.writestr(name, b"x")
            with self.subTest(name=name), self.assertRaises(ValueError):
                inspect_package(self.path)

    def test_stage_detects_changed_file_after_selection(self):
        package = inspect_package(write_package(self.path))
        write_package(self.path, "4.19")
        target = self.root / "staged"
        target.mkdir()
        with self.assertRaisesRegex(ValueError, "thay đổi"):
            stage_package(package, target)

    def test_latest_is_numeric_and_ignores_invalid_files(self):
        write_package(self.path, "4.9")
        write_package(self.root / "new.tipa", "4.18")
        (self.root / "invalid.tipa").write_bytes(b"not a package")
        write_package(self.root / "other.ipa", "99.0", "other.app")
        self.assertEqual(find_latest_package(self.root).version, "4.18")
        self.assertEqual(version_key("4.18.0"), version_key("4.18"))

    def test_corrupt_archive_rejected(self):
        self.path.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            inspect_package(self.path)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.package = inspect_package(write_package(Path(self.tmp.name) / "app.tipa"))
        self.server = UpdateServer(self.package, host="127.0.0.1")
        await self.server.start()
        self.job = self.server.add_job()

    async def asyncTearDown(self):
        await self.server.stop()
        self.tmp.cleanup()

    async def request(self, method, route, body=b""):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        writer.write((f"{method} {route} HTTP/1.1\r\nHost: local\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        return data.partition(b"\r\n\r\n")

    async def test_serves_exact_bytes_and_counts_completed_get_only(self):
        head, _, body = await self.request("HEAD", f"/{self.job}/ControlIOS.tipa")
        self.assertIn(b"200", head)
        self.assertEqual(body, b"")
        self.assertFalse(self.server.hits)
        head, _, body = await self.request("GET", f"/{self.job}/ControlIOS.tipa")
        self.assertEqual(body, self.package.path.read_bytes())
        self.assertEqual(self.server.hits[self.job], 1)

    async def test_bounded_status_with_matching_job_version(self):
        status = {"job": self.job, "state": "installing", "version": "4.18", "message": "Installing"}
        head, _, _ = await self.request("POST", f"/{self.job}/status", json.dumps(status).encode())
        self.assertIn(b"200", head)
        self.assertEqual(self.server.jobs[self.job]["state"], "installing")
        for changed in ({"job": "other"}, {"version": "999"}, {"state": []}, {"message": "x" * 5000}):
            head, _, _ = await self.request("POST", f"/{self.job}/status", json.dumps(status | changed).encode())
            self.assertIn(b"400", head)
            self.assertEqual(self.server.jobs[self.job]["state"], "installing")

    async def test_no_arbitrary_file_or_guessed_job_access(self):
        for route in ("/app.tipa", "/other/ControlIOS.tipa", f"/{self.job}/../ControlIOS.tipa", f"/{self.job}/status.json"):
            head, _, _ = await self.request("GET", route)
            self.assertIn(b"404", head)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.package = inspect_package(write_package(Path(self.tmp.name) / "app.tipa", "4.19"))
        self.server = UpdateServer(self.package)
        self.server.port = 5555
        self.channel = SimpleNamespace(host="172.30.2.42", server_version=AsyncMock(return_value="4.18"),
            command=AsyncMock(), install_ipa=AsyncMock(), find_trollstore=AsyncMock(return_value="com.opa334.TrollStore"))
        self.events = []
        self.verify = AsyncMock(return_value=True)
        self.route_patch = patch("controlios.ios_update.local_ip", return_value="172.30.0.91")
        self.route_patch.start()

    async def asyncTearDown(self):
        self.route_patch.stop()
        self.tmp.cleanup()

    async def flow(self, **kwargs):
        return await update_one(self.channel, self.package, self.server, 5901,
            self.events.append, timeout=.015, interval=.001, verify_vnc=self.verify, **kwargs)

    def commands(self, result="new"):
        async def command(line):
            if line == "updateios check": return "OK LAN_UPDATE_1"
            job = line.split()[1]
            if result == "error":
                self.server.jobs[job] = {"state": "error", "message": "SHA256 mismatch"}
            elif result == "new":
                self.channel.server_version.return_value = "4.19"
            elif result == "ambiguous":
                self.channel.server_version.return_value = "4.19"
                raise ControlError("mất kết nối giữa chừng")
            else:
                self.server.jobs[job] = {"state": "installed", "message": "Installed"}
            return f"OK {job}"
        self.channel.command.side_effect = command

    async def test_native_update_verifies_new_running_version_and_vnc(self):
        self.commands()
        self.assertEqual(await self.flow(), "updated")
        self.channel.install_ipa.assert_not_called()
        self.verify.assert_awaited_once_with("172.30.2.42", 5901)
        self.assertIn("http://172.30.0.91:5555/", self.channel.command.call_args.args[0])

    async def test_callback_alone_never_means_success(self):
        self.commands("status-only")
        with self.assertRaisesRegex(ControlError, "Hết thời gian"):
            await self.flow()
        self.verify.assert_not_called()

    async def test_error_callback_stops_without_another_install(self):
        self.commands("error")
        with self.assertRaisesRegex(ControlError, "SHA256"):
            await self.flow()
        self.assertEqual(self.channel.command.await_count, 2)

    async def test_ambiguous_reply_monitored_without_duplicate_dispatch(self):
        self.commands("ambiguous")
        self.assertEqual(await self.flow(), "updated")
        self.assertEqual(self.channel.command.await_count, 2)

    async def test_stalled_vnc_is_not_reported_as_success(self):
        self.commands()
        self.verify.return_value = False
        with self.assertRaises(ControlError):
            await self.flow()

    async def test_already_newer_skipped_without_dispatch(self):
        self.channel.server_version.return_value = "4.20"
        self.assertEqual(await self.flow(), "skipped")
        self.channel.command.assert_not_called()
        self.channel.install_ipa.assert_not_called()

    async def test_old_version_uses_single_trollstore_request(self):
        self.channel.server_version.return_value = "4.14"
        async def install(url): self.channel.server_version.return_value = "4.19"
        self.channel.install_ipa.side_effect = install
        self.assertEqual(await self.flow(), "updated")
        self.channel.install_ipa.assert_awaited_once()
        self.channel.command.assert_not_called()
        self.assertTrue(any("lần đầu" in event for event in self.events))

    async def test_usb_loopback_rejected(self):
        self.channel.host = "127.0.0.1"
        with self.assertRaisesRegex(ControlError, "Wi-Fi"):
            await self.flow()
        self.channel.command.assert_not_called()

    async def test_missing_trollstore_is_explicit_error(self):
        self.channel.server_version.return_value = "4.14"
        self.channel.find_trollstore.return_value = None
        with self.assertRaisesRegex(ControlError, "TrollStore"):
            await self.flow()
        self.channel.install_ipa.assert_not_called()


class VNCVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_initial_exchange_required(self):
        async def serve(reader, writer):
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            self.assertEqual(await reader.readexactly(12), b"RFB 003.008\n")
            writer.write(b"\x02\x01\x02")
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            self.assertTrue(await vnc_ready("127.0.0.1", server.sockets[0].getsockname()[1]))
        finally:
            server.close()
            await server.wait_closed()

    async def test_open_port_with_no_rfb_is_not_ready(self):
        async def serve(reader, writer): writer.close()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            self.assertFalse(await vnc_ready("127.0.0.1", server.sockets[0].getsockname()[1]))
        finally:
            server.close()
            await server.wait_closed()
