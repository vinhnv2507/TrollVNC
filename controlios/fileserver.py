"""Web server tí hon để iPhone tải file từ PC.

Dùng cho việc cài `.ipa` hàng loạt: Control IOS phục vụ file, rồi bảo từng máy
mở `apple-magnifier://install?url=http://<ip-pc>:<cổng>/<tên file>` để TrollStore
tự tải và cài.

Cố tình viết bằng asyncio thuần, không thêm phụ thuộc: nó chỉ phục vụ đúng
những file ta chủ động đăng ký, theo phương thức GET và HEAD.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import unquote

log = logging.getLogger(__name__)

CHUNK = 256 * 1024


def local_ip(target: str = "8.8.8.8") -> str:
    """Địa chỉ LAN mà máy khác nhìn thấy được.

    Không thể dùng ``127.0.0.1``: iPhone phải tải từ PC qua mạng.
    """

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((target, 80))     # UDP: không thật sự gửi gói nào
        return probe.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()


@dataclass
class FileServer:
    """Phục vụ một tập file đã đăng ký, trên mọi giao diện mạng."""

    port: int = 0
    host: str = "0.0.0.0"

    files: Dict[str, Path] = field(default_factory=dict)
    hits: Dict[str, int] = field(default_factory=dict)

    _server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("File server on %s:%d", self.host, self.port)
        return self.port

    async def stop(self) -> None:
        if not self._server:
            return
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=5)
        except asyncio.TimeoutError:
            pass
        self._server = None

    def add(self, path: Path | str) -> str:
        """Đăng ký một file, trả về tên dùng trong URL."""

        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        name = path.name
        self.files[name] = path
        self.hits.setdefault(name, 0)
        return name

    def url_for(self, name: str, host: Optional[str] = None) -> str:
        return f"http://{host or local_ip()}:{self.port}/{name}"

    # ------------------------------------------------------------------ HTTP

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=10)
            parts = request.decode("latin-1").split()
            if len(parts) < 2:
                return
            method, raw_target = parts[0].upper(), parts[1]

            # Bỏ phần header còn lại.
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line in (b"\r\n", b"\n", b""):
                    break

            name = unquote(raw_target.lstrip("/").split("?", 1)[0])
            path = self.files.get(name)

            if method not in ("GET", "HEAD") or path is None or not path.is_file():
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n"
                             b"Connection: close\r\n\r\n")
                await writer.drain()
                return

            size = path.stat().st_size
            header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/octet-stream\r\n"
                f"Content-Length: {size}\r\n"
                f'Content-Disposition: attachment; filename="{name}"\r\n'
                "Connection: close\r\n\r\n"
            )
            writer.write(header.encode("latin-1"))
            await writer.drain()

            if method == "HEAD":
                return

            self.hits[name] = self.hits.get(name, 0) + 1
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("file server error")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
