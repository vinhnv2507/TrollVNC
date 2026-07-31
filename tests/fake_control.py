"""Giả lập control socket của TrollVNC đã vá — để test không cần iPhone.

Bám sát hành vi thật đọc được từ mã nguồn TrollVNC: mỗi kết nối phục vụ đúng
một dòng lệnh rồi đóng, và kết nối không phải loopback phải có tiền tố
``auth <token> ``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


@dataclass
class FakeControlServer:
    token: str = "Congavinh1"

    #: bundle id -> (tên, loại, phiên bản)
    apps: Dict[str, Tuple[str, str, str]] = field(
        default_factory=lambda: {
            "com.facebook.Facebook": ("Facebooku", "User", "480.0"),
            "com.golike.app": ("GoLike", "User", "2.1.0"),
            "io.grass.app": ("Grass", "User", "1.4"),
            "com.honeygain.app": ("Honeygain", "User", "3.0"),
            "com.apple.Preferences": ("Cài đặt", "System", "1.0"),
        }
    )
    running: Set[str] = field(default_factory=lambda: {"com.golike.app"})

    launched: List[str] = field(default_factory=list)
    terminated: List[str] = field(default_factory=list)
    unauthorized: int = 0
    #: Đặt True để giả lập bản TrollVNC gốc (chưa vá).
    unpatched: bool = False

    _server: asyncio.AbstractServer | None = None
    port: int = 0

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, host, port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readline()
            cmd = raw.decode("utf-8", errors="replace").strip()

            # Bản thật chỉ bắt token với kết nối ngoài loopback; test luôn chạy
            # qua loopback nên ta bắt token vô điều kiện để kiểm tra được nhánh
            # xác thực của client.
            prefix = f"auth {self.token} "
            if cmd.startswith(prefix):
                cmd = cmd[len(prefix):].strip()
            else:
                self.unauthorized += 1
                writer.write(b"ERR Unauthorized\n")
                await writer.drain()
                return

            writer.write(self._respond(cmd))
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    def _respond(self, cmd: str) -> bytes:
        if cmd == "count":
            return b"1\n"

        if self.unpatched and cmd.split(" ")[0] in ("apps", "launch", "terminate"):
            return b"ERR Unknown\n"

        if cmd == "apps":
            rows = "".join(
                f"{bid}\t{name}\t{kind}\t{ver}\n"
                for bid, (name, kind, ver) in self.apps.items()
            )
            return rows.encode("utf-8")

        if cmd.startswith("launch "):
            bundle = cmd[len("launch "):].strip()
            if bundle not in self.apps:
                return b"ERR LaunchFailed\n"
            self.launched.append(bundle)
            self.running.add(bundle)
            return b"OK\n"

        if cmd.startswith("terminate "):
            bundle = cmd[len("terminate "):].strip()
            if bundle not in self.running:
                return b"NOT_RUNNING\n"
            self.running.discard(bundle)
            self.terminated.append(bundle)
            return b"OK\n"

        return b"ERR Unknown\n"
