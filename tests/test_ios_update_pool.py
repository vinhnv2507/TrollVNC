"""Exercise the complete pool pipeline with real local HTTP and fake phones."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch

from controlios.config import Settings, DeviceSpec
from controlios.control_channel import ControlError
from controlios.vnc.pool import DevicePool
from controlios.ios_update import inspect_package, update_one
from tests.test_ios_update import write_package


class PoolUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_serves_verified_copy_reports_failure_and_releases_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = inspect_package(write_package(Path(temporary) / "package.tipa", "4.19"))
            pool = DevicePool(Settings(), lambda *args: None, lambda *args: None)
            pool._loop = asyncio.get_running_loop()
            keys = [f"172.30.2.{index}:5901" for index in range(40, 46)]
            pool._specs = {key: DeviceSpec(key.split(":")[0]) for key in keys}
            finished = asyncio.Event()
            result = []
            events = []
            addresses = []
            active = 0
            peak = 0
            legacy_requests = []

            async def http(url, method="GET", body=b""):
                address = urlsplit(url)
                addresses.append(address.port)
                reader, writer = await asyncio.open_connection("127.0.0.1", address.port)
                writer.write((f"{method} {address.path} HTTP/1.1\r\nHost: local\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
                await writer.drain()
                response = await reader.read()
                writer.close()
                await writer.wait_closed()
                head, _, payload = response.partition(b"\r\n\r\n")
                self.assertIn(b"200", head)
                return payload

            class Phone:
                def __init__(self, key):
                    self.host = key.split(":")[0]
                    self.version = "4.18"

                async def server_version(self): return self.version

                async def command(self, line):
                    nonlocal active, peak
                    if line == "updateios check":
                        if self.host.endswith(".44"):
                            raise ControlError("ERR LocalUpdateUnavailable TrollStore/helper missing")
                        return "OK LAN_UPDATE_1"
                    _, job, version, sha, url = line.split()
                    active += 1
                    peak = max(peak, active)
                    try:
                        downloaded = await http(url)
                        self_check = hashlib.sha256(downloaded).hexdigest()
                        if self_check != sha: raise AssertionError("Served copy changed")
                        await asyncio.sleep(.02)
                        failure = self.host.endswith(".45")
                        status = {"job": job, "version": version,
                                  "state": "error" if failure else "installed",
                                  "message": "Synthetic TrollStore failure" if failure else "Installed"}
                        await http(url.rsplit("/", 1)[0] + "/status", "POST", json.dumps(status).encode())
                        if not failure: self.version = version
                        return f"OK {job}"
                    finally:
                        active -= 1

                async def find_trollstore(self): return "com.opa334.TrollStore"

                async def install_ipa(self, url):
                    nonlocal active, peak
                    legacy_requests.append(self.host)
                    active += 1
                    peak = max(peak, active)
                    try:
                        downloaded = await http(url)
                        if hashlib.sha256(downloaded).hexdigest() != package.sha256:
                            raise AssertionError("Fallback served a changed package")
                        self.version = package.version
                    finally:
                        active -= 1

            phones = {key: Phone(key) for key in keys}
            pool._channel = lambda key: phones[key]

            async def instrumented(*args, **kwargs):
                async def ready(*unused): return True
                return await update_one(*args, **kwargs, timeout=2, interval=.001, verify_vnc=ready)

            def done(*args):
                result.append(args)
                finished.set()

            with patch("controlios.ios_update.local_ip", return_value="172.30.0.91"), patch("controlios.ios_update.update_one", instrumented):
                pool.update_ios(keys, package, on_event=lambda *args: events.append(args), on_done=done)
                self.assertEqual(pool.updating_ios, set(keys))
                with self.assertRaises(RuntimeError):
                    pool.update_ios(keys, package)
                await asyncio.wait_for(finished.wait(), 10)
            self.assertFalse(pool.updating_ios)
            self.assertEqual(result[0][1], 5)
            self.assertEqual(result[0][2], [(keys[-1], "Synthetic TrollStore failure")])
            self.assertEqual(legacy_requests, ["172.30.2.44"])
            self.assertGreater(peak, 1)
            self.assertLessEqual(peak, 3)
            self.assertEqual(len(set(addresses)), 1)
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", addresses[0])
            pool._loop = None
