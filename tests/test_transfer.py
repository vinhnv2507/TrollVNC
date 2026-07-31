"""Truyền file lên máy, và cài .ipa qua web server trên PC."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.control_channel import ControlChannel, ControlError  # noqa: E402
from controlios.fileserver import FileServer, local_ip              # noqa: E402
from tests.fake_control import FakeControlServer                    # noqa: E402


class PutFileTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeControlServer()
        port = await self.server.start()
        self.channel = ControlChannel("127.0.0.1", port, self.server.token, timeout=5)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        self.tmp.cleanup()

    def _file(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    async def test_small_file_arrives_byte_for_byte(self) -> None:
        payload = b"xin chao\n\x00\x01\x02 nhi phan"
        local = self._file("a.bin", payload)

        written = await self.channel.put_file(local, "/var/mobile/Documents/a.bin")

        self.assertEqual(written, len(payload))
        self.assertEqual(self.server.received["/var/mobile/Documents/a.bin"], payload)

    async def test_file_larger_than_one_chunk(self) -> None:
        """Quan trọng: file phải qua nhiều lượt đọc mà không mất/đảo byte."""

        payload = bytes(range(256)) * 2000        # 512 KB, có mọi giá trị byte
        local = self._file("big.bin", payload)

        written = await self.channel.put_file(local, "/var/mobile/Documents/big.bin")

        self.assertEqual(written, len(payload))
        self.assertEqual(self.server.received["/var/mobile/Documents/big.bin"], payload)

    async def test_empty_file_is_allowed(self) -> None:
        local = self._file("empty.bin", b"")
        written = await self.channel.put_file(local, "/var/mobile/Documents/empty.bin")
        self.assertEqual(written, 0)
        self.assertEqual(self.server.received["/var/mobile/Documents/empty.bin"], b"")

    async def test_progress_is_reported_and_ends_at_the_total(self) -> None:
        payload = b"x" * 200_000
        local = self._file("p.bin", payload)
        seen = []

        await self.channel.put_file(local, "/var/mobile/Documents/p.bin",
                                    progress=lambda done, total: seen.append((done, total)))

        self.assertTrue(seen)
        self.assertEqual(seen[-1], (len(payload), len(payload)))
        self.assertTrue(all(d <= t for d, t in seen))

    async def test_relative_path_is_refused_by_the_device(self) -> None:
        local = self._file("a.bin", b"x")
        with self.assertRaises(ControlError) as ctx:
            await self.channel.put_file(local, "var/mobile/a.bin")
        self.assertIn("BadPath", str(ctx.exception))

    async def test_parent_traversal_is_refused(self) -> None:
        local = self._file("a.bin", b"x")
        with self.assertRaises(ControlError):
            await self.channel.put_file(local, "/var/mobile/../../etc/passwd")

    async def test_unpatched_device_says_so(self) -> None:
        self.server.unpatched = True
        local = self._file("a.bin", b"x")
        with self.assertRaises(ControlError):
            await self.channel.put_file(local, "/var/mobile/Documents/a.bin")


class OpenUrlTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeControlServer()
        port = await self.server.start()
        self.channel = ControlChannel("127.0.0.1", port, self.server.token, timeout=5)

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_open_url_reaches_the_device(self) -> None:
        await self.channel.open_url("https://example.com/a b")
        self.assertEqual(self.server.opened_urls, ["https://example.com/a b"])

    async def test_install_ipa_uses_the_trollstore_scheme(self) -> None:
        await self.channel.install_ipa("http://192.168.1.5:8080/App Store.ipa")

        self.assertEqual(len(self.server.opened_urls), 1)
        url = self.server.opened_urls[0]
        self.assertTrue(url.startswith("apple-magnifier://install?url="), url)
        # Dấu cách phải được mã hoá, nếu không TrollStore cắt URL giữa chừng.
        self.assertIn("%20", url)
        self.assertNotIn(" ", url)
        # Nhưng dấu hai chấm và gạch chéo của URL phải giữ nguyên.
        self.assertIn("http://192.168.1.5:8080/", url)


class FileServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.server = FileServer(host="127.0.0.1")
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        self.tmp.cleanup()

    async def _get(self, target: str, method: str = "GET") -> tuple[str, bytes]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        writer.write(f"{method} {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=10)
        writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        return head.decode("latin-1"), body

    async def test_serves_a_registered_file_intact(self) -> None:
        payload = bytes(range(256)) * 1500       # ~384 KB nhị phân
        path = self.dir / "app.ipa"
        path.write_bytes(payload)
        name = self.server.add(path)

        head, body = await self._get(f"/{name}")

        self.assertIn("200 OK", head)
        self.assertIn(f"Content-Length: {len(payload)}", head)
        self.assertEqual(body, payload)

    async def test_counts_downloads(self) -> None:
        path = self.dir / "app.ipa"
        path.write_bytes(b"x")
        name = self.server.add(path)

        self.assertEqual(self.server.hits[name], 0)
        await self._get(f"/{name}")
        await self._get(f"/{name}")
        self.assertEqual(self.server.hits[name], 2)

    async def test_head_does_not_count_as_a_download(self) -> None:
        path = self.dir / "app.ipa"
        path.write_bytes(b"x" * 10)
        name = self.server.add(path)

        head, body = await self._get(f"/{name}", method="HEAD")

        self.assertIn("200 OK", head)
        self.assertIn("Content-Length: 10", head)
        self.assertEqual(body, b"")
        self.assertEqual(self.server.hits[name], 0)

    async def test_unregistered_paths_are_404(self) -> None:
        head, _ = await self._get("/khong-co.ipa")
        self.assertIn("404", head)

    async def test_does_not_expose_arbitrary_files(self) -> None:
        """Chỉ phục vụ file đã đăng ký, không phải cả ổ đĩa."""

        secret = self.dir / "secret.txt"
        secret.write_text("khong duoc lo")
        self.server.add(self.dir / "app.ipa") if (self.dir / "app.ipa").exists() else None

        for target in ("/secret.txt", "/../secret.txt", "/%2e%2e/secret.txt"):
            head, body = await self._get(target)
            self.assertIn("404", head, f"{target} không được phục vụ")
            self.assertNotIn(b"khong duoc lo", body)

    async def test_url_for_uses_a_routable_address(self) -> None:
        path = self.dir / "app.ipa"
        path.write_bytes(b"x")
        name = self.server.add(path)

        url = self.server.url_for(name, "192.168.1.5")
        self.assertEqual(url, f"http://192.168.1.5:{self.server.port}/app.ipa")

        # local_ip() phải trả về địa chỉ máy khác gọi được, không phải loopback.
        self.assertFalse(local_ip().startswith("127."), local_ip())

    async def test_adding_a_missing_file_fails_early(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.server.add(self.dir / "khong-ton-tai.ipa")


if __name__ == "__main__":
    unittest.main(verbosity=2)
