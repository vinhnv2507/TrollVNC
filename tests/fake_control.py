"""Giả lập control socket của TrollVNC đã vá — để test không cần iPhone.

Bám sát hành vi thật đọc được từ mã nguồn TrollVNC: mỗi kết nối phục vụ đúng
một dòng lệnh rồi đóng, và kết nối không phải loopback phải có tiền tố
``auth <token> ``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
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
            "com.opa334.TrollStore": ("TrollStore", "User", "2.1.1"),
            "com.apple.Preferences": ("Cài đặt", "System", "1.0"),
        }
    )
    running: Set[str] = field(default_factory=lambda: {"com.golike.app"})
    frontmost: str | None = "com.golike.app"

    launched: List[str] = field(default_factory=list)
    terminated: List[str] = field(default_factory=list)
    opened_urls: List[str] = field(default_factory=list)
    #: (bundle id, url) của các lệnh openurlin
    opened_in: List[Tuple[str, str]] = field(default_factory=list)
    #: đường dẫn -> nội dung đã nhận
    received: Dict[str, bytes] = field(default_factory=dict)
    #: clipboard hiện tại của "máy"
    clipboard: str = ""
    #: đường dẫn các ảnh đã được nạp vào Thư viện qua savephoto
    saved_photos: List[str] = field(default_factory=list)
    respring_count: int = 0
    scale: float = 1.0
    #: keeperd (daemon canh ControlIOS) và app Keeper có đang chạy không
    keeperd_running: bool = True
    keeper_app_running: bool = False
    #: số lần nhận `keeper start`
    keeper_starts: int = 0
    keeper_start_noop: bool = False
    reboot_count: int = 0
    shutdown_count: int = 0
    locked: bool = False
    home_count: int = 0
    #: bundle id đã bị xoá dữ liệu; (bundle, tên) đã khôi phục
    wiped: List[str] = field(default_factory=list)
    restored: List[Tuple[str, str]] = field(default_factory=list)
    #: bundle -> {tên snapshot: (epoch, size)}
    snapshots: Dict[str, Dict[str, Tuple[int, int]]] = field(default_factory=dict)
    _snap_counter: int = 0
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

            if cmd.startswith("put "):
                writer.write(await self._receive(reader, cmd[len("put "):]))
            elif cmd.startswith("clipset "):
                writer.write(await self._receive_clip(reader, cmd[len("clipset "):]))
            elif cmd.startswith("getfile "):
                writer.write(self._get_file(cmd[len("getfile "):].strip()))
            else:
                writer.write(self._respond(cmd))
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def _receive(self, reader: asyncio.StreamReader, spec: str) -> bytes:
        """`put <size> <path>` — đọc đúng size byte rồi ghi nhớ lại."""

        if self.unpatched:
            return b"ERR Unknown\n"
        size_text, _, path = spec.strip().partition(" ")
        try:
            size = int(size_text)
        except ValueError:
            return b"ERR Usage put <size> <path>\n"
        if not path.startswith("/") or ".." in path:
            return b"ERR BadPath\n"
        try:
            data = await asyncio.wait_for(reader.readexactly(size), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return b"ERR Incomplete\n"
        self.received[path] = data
        return f"OK {len(data)}\n".encode()

    async def _receive_clip(self, reader: asyncio.StreamReader, spec: str) -> bytes:
        """`clipset <size>` — đọc đúng size byte rồi đặt làm clipboard."""

        if self.unpatched:
            return b"ERR Unknown\n"
        try:
            size = int(spec.strip())
        except ValueError:
            return b"ERR Usage clipset <size>\n"
        try:
            data = await asyncio.wait_for(reader.readexactly(size), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return b"ERR Incomplete\n"
        self.clipboard = data.decode("utf-8", errors="replace")
        return f"OK {len(data)}\n".encode()

    def _respond(self, cmd: str) -> bytes:
        if cmd == "count":
            return b"1\n"

        if cmd.startswith("ls "):
            path = cmd[len("ls "):].rstrip("/")
            children = {}
            prefix = path + "/"
            for full, data in self.received.items():
                if not full.startswith(prefix):
                    continue
                rest = full[len(prefix):]
                name, slash, _tail = rest.partition("/")
                children[name] = (0, True) if slash else (len(data), False)
            return "".join(
                f"{name}\t{size}\t{1 if is_dir else 0}\n"
                for name, (size, is_dir) in children.items()).encode()

        if cmd == "clipget":
            if self.unpatched:
                return b"ERR Unknown\n"
            payload = self.clipboard.encode("utf-8")
            return b"OK " + str(len(payload)).encode() + b"\n" + payload

        if cmd.startswith("savephoto "):
            if self.unpatched:
                return b"ERR Unknown\n"
            path = cmd[len("savephoto "):].strip()
            if path not in self.received:
                return b"ERR NotFound\n"
            self.saved_photos.append(path)
            return b"OK\n"

        if cmd == "respring":
            if self.unpatched:
                return b"ERR Unknown\n"
            self.respring_count += 1
            return b"OK\n"

        if cmd.startswith("setscale "):
            if self.unpatched:
                return b"ERR Unknown\n"
            try:
                value = float(cmd[len("setscale "):].strip())
            except ValueError:
                return b"ERR BadScale\n"
            if not (0.0 < value <= 1.0):
                return b"ERR BadScale\n"
            self.scale = value
            return f"OK {value:.3f}\n".encode()

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
            self.frontmost = bundle
            return b"OK\n"

        if cmd == "frontmost":
            return f"OK {self.frontmost or 'none'}\n".encode()

        if cmd.startswith("container "):
            bundle = cmd[len("container "):].strip()
            if bundle not in self.apps:
                return b"NOT_FOUND\n"
            data = f"/var/mobile/Containers/Data/Application/UUID-{bundle}"
            bundle_dir = f"/var/containers/Bundle/Application/UUID-{bundle}"
            return f"{data}\t{bundle_dir}\n".encode()

        if cmd.startswith("ls "):
            path = cmd[len("ls "):].strip()
            if not path.startswith("/"):
                return b"ERR BadPath\n"
            rows = "".join(
                f"{Path(p).name}\t{len(data)}\t0\n"
                for p, data in self.received.items()
                if str(Path(p).parent).replace("\\", "/") == path.rstrip("/")
            )
            return rows.encode()

        if cmd.startswith("openurlin "):
            bundle, _, url = cmd[len("openurlin "):].strip().partition(" ")
            if not url:
                return b"ERR Usage openurlin <bundle id> <url>\n"
            self.opened_in.append((bundle, url))
            return b"OK\n"

        if cmd.startswith("openurl "):
            self.opened_urls.append(cmd[len("openurl "):].strip())
            return b"OK\n"

        if cmd.startswith("terminate "):
            bundle = cmd[len("terminate "):].strip()
            if bundle not in self.running:
                return b"NOT_RUNNING\n"
            self.running.discard(bundle)
            self.terminated.append(bundle)
            return b"OK\n"

        if cmd.startswith("wipeapp "):
            if self.unpatched:
                return b"ERR Unknown\n"
            bundle = cmd[len("wipeapp "):].strip()
            if bundle not in self.apps:
                return b"NOT_FOUND\n"
            self.wiped.append(bundle)
            return b"OK\n"

        if cmd.startswith("snapshot "):
            if self.unpatched:
                return b"ERR Unknown\n"
            rest = cmd[len("snapshot "):].strip()
            bundle, _, name = rest.partition(" ")
            name = name.strip()
            if bundle not in self.apps:
                return b"NOT_FOUND\n"
            self._snap_counter += 1     # tăng mọi lần -> epoch tăng dần theo thứ tự lưu
            if not name:
                name = f"snap-{self._snap_counter}"
            self.snapshots.setdefault(bundle, {})[name] = (1_700_000_000 + self._snap_counter, 4096)
            return f"OK {name}\n".encode()

        if cmd.startswith("snaplist "):
            if self.unpatched:
                return b"ERR Unknown\n"
            bundle = cmd[len("snaplist "):].strip()
            rows = "".join(
                f"{name}\t{epoch}\t{size}\n"
                for name, (epoch, size) in self.snapshots.get(bundle, {}).items()
            )
            return rows.encode()

        if cmd.startswith("snapclear "):
            if self.unpatched:
                return b"ERR Unknown\n"
            bundle = cmd[len("snapclear "):].strip()
            self.snapshots.pop(bundle, None)
            return b"OK\n"

        if cmd.startswith("snapdel "):
            if self.unpatched:
                return b"ERR Unknown\n"
            bundle, _, name = cmd[len("snapdel "):].strip().partition(" ")
            name = name.strip()
            if name not in self.snapshots.get(bundle, {}):
                return b"NOT_FOUND\n"
            del self.snapshots[bundle][name]
            return b"OK\n"

        if cmd == "keeper status":
            if self.unpatched:
                return b"ERR Unknown\n"
            flags = ""
            if self.keeperd_running:
                flags += " keeperd"
            if self.keeper_app_running:
                flags += " app"
            return f"OK{flags}\n".encode()

        if cmd == "keeper start":
            if self.unpatched:
                return b"ERR Unknown\n"
            self.keeper_starts += 1
            if self.keeperd_running:
                return b"OK da chay san\n"
            if self.keeper_start_noop:
                return b"OK spawn keeperd\n"
            self.keeperd_running = True
            return b"OK spawn keeperd\n"

        if cmd == "reboot":
            self.reboot_count += 1
            return b"OK rebooting\n"

        if cmd == "shutdown":
            self.shutdown_count += 1
            return b"OK shutting down\n"

        if cmd == "wakeiflocked":
            if self.unpatched:
                return b"ERR Unknown\n"
            if not self.locked:
                return b"OK unlocked\n"
            self.home_count += 1
            self.locked = False
            return b"OK home\n"

        if cmd.startswith(("setinflight ", "setdefer ", "setorient ")):
            if self.unpatched:
                return b"ERR Unknown\n"
            self.smoothness = cmd
            return b"OK\n"

        if cmd.startswith("setprelude "):
            if self.unpatched:
                return b"ERR Unknown\n"
            self.prelude = cmd[len("setprelude "):].strip()
            return b"OK\n"

        if cmd.startswith("color "):
            if self.unpatched:
                return b"ERR Unknown\n"
            parts = cmd.split()
            if len(parts) < 3:
                return b"ERR Args\n"
            return f"OK {getattr(self, 'color_hex', 'FBBC05')}\n".encode()

        if cmd.startswith("restore "):
            if self.unpatched:
                return b"ERR Unknown\n"
            bundle, _, name = cmd[len("restore "):].strip().partition(" ")
            name = name.strip()
            if bundle not in self.apps:
                return b"NOT_FOUND\n"
            if name not in self.snapshots.get(bundle, {}):
                return b"ERR NoSnapshot\n"
            self.restored.append((bundle, name))
            return b"OK\n"

        return b"ERR Unknown\n"

    def _get_file(self, path: str) -> bytes:
        if self.unpatched:
            return b"ERR Unknown\n"
        if not path.startswith("/var/mobile/controlios-snap/"):
            return b"ERR BadPath\n"
        data = self.received.get(path)
        if data is None:
            return b"ERR CannotRead\n"
        return f"OK {len(data)}\n".encode() + data
