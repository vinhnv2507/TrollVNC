"""Install a verified local ControlIOS package over LAN, then verify the daemon."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import plistlib
import re
import secrets
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .control_channel import ControlError, UnauthorizedError, NotPatchedError
from .fileserver import FileServer, local_ip

BUNDLE_ID = "com.controlios.app"
MAX_PACKAGE_SIZE = 128 * 1024 * 1024
STATES = {"downloading", "installing", "restarting", "installed", "error"}


def version_key(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or len(value) > 32 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value):
        raise ValueError("Phiên bản ControlIOS không hợp lệ")
    numbers = [int(part) for part in value.split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


@dataclass(frozen=True)
class IOSPackage:
    path: Path
    version: str
    sha256: str
    size: int


def inspect_package(path: str | Path) -> IOSPackage:
    path = Path(path)
    if path.suffix.lower() not in {".tipa", ".ipa"}:
        raise ValueError("Hãy chọn gói ControlIOS .tipa hoặc .ipa")
    size = path.stat().st_size
    if not 0 < size <= MAX_PACKAGE_SIZE:
        raise ValueError("Gói rỗng hoặc lớn hơn 128 MB")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any("\\" in n or ".." in n.split("/") or n.startswith("/") for n in names):
                raise ValueError("Đường dẫn trong gói không hợp lệ")
            roots = {"/".join(n.split("/")[:2]) for n in names
                     if n.startswith("Payload/") and len(n.split("/")) > 2 and n.split("/")[1].endswith(".app")}
            if len(roots) != 1:
                raise ValueError("Gói phải chứa đúng một ứng dụng")
            root = next(iter(roots))
            if archive.getinfo(root + "/Info.plist").file_size > 1024 * 1024:
                raise ValueError("Info.plist quá lớn")
            if sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
                raise ValueError("Nội dung giải nén quá lớn")
            info = plistlib.loads(archive.read(root + "/Info.plist"))
            if info.get("CFBundleIdentifier") != BUNDLE_ID:
                raise ValueError("Gói này không phải ControlIOS cho iOS")
            version = info.get("CFBundleShortVersionString", "")
            version_key(version)
            for executable in ("trollvncserver", "trollvncmanager", info.get("CFBundleExecutable", "")):
                if not executable or "/" in executable or root + "/" + executable not in names:
                    raise ValueError("Thiếu chương trình của ControlIOS trong gói")
            if version_key(version) >= version_key("4.18") and root + "/controliosupdater" not in names:
                raise ValueError("Gói thiếu thành phần cập nhật qua LAN")
            bad = archive.testzip()
            if bad:
                raise ValueError("Gói bị hỏng (CRC)")
    except (zipfile.BadZipFile, KeyError, plistlib.InvalidFileException, TypeError, AttributeError) as exc:
        raise ValueError("Gói ControlIOS bị hỏng hoặc thiếu thông tin") from exc
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return IOSPackage(path, version, digest, size)


def find_latest_package(directory: Path) -> IOSPackage | None:
    """Only inspect packages in the folder the user selected (no online lookup)."""
    packages = []
    if directory.is_dir():
        try:
            paths = list(directory.iterdir())
        except OSError:
            return None
        for path in paths:
            if path.suffix.lower() in {".ipa", ".tipa"} and path.is_file():
                try:
                    packages.append(inspect_package(path))
                except (ValueError, OSError):
                    pass
    return max(packages, key=lambda p: version_key(p.version), default=None)


def stage_package(package: IOSPackage, directory: Path) -> IOSPackage:
    destination = directory / "ControlIOS.tipa"
    shutil.copyfile(package.path, destination)
    staged = inspect_package(destination)
    if staged.version != package.version or staged.sha256 != package.sha256:
        raise ValueError("Gói đã thay đổi sau khi chọn; hãy chọn lại")
    return staged


class UpdateServer(FileServer):
    """Opaque per-device URLs and bounded progress callbacks from the helper."""
    def __init__(self, package: IOSPackage, **kwargs):
        super().__init__(**kwargs)
        self.package = package
        self.jobs: dict[str, dict] = {}
        self._handlers: set[asyncio.Task] = set()

    def add_job(self) -> str:
        job = secrets.token_hex(16)
        self.jobs[job] = {}
        self.files[f"{job}/ControlIOS.tipa"] = self.package.path
        return job

    async def stop(self) -> None:
        await super().stop()
        for task in list(self._handlers):
            task.cancel()
        await asyncio.gather(*list(self._handlers), return_exceptions=True)

    async def _handle(self, reader, writer) -> None:
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            parts = line.decode("ascii").split()
            if len(parts) != 3:
                return
            method, route, _ = parts
            headers = {}
            for _ in range(40):
                line = await asyncio.wait_for(reader.readline(), 10)
                if line in (b"\r\n", b"\n", b""):
                    break
                name, value = line.decode("ascii").split(":", 1)
                headers[name.lower()] = value.strip()
            else:
                return
            job, _, leaf = route.lstrip("/").partition("/")
            if job not in self.jobs or route != f"/{job}/{leaf}":
                await self._reply(writer, 404)
            elif method == "POST" and leaf == "status":
                length = int(headers.get("content-length", "0"))
                if not 0 < length <= 4096 or "transfer-encoding" in headers:
                    await self._reply(writer, 400)
                    return
                data = json.loads(await asyncio.wait_for(reader.readexactly(length), 10))
                if (not isinstance(data, dict) or data.get("job") != job or
                        data.get("version") != self.package.version or not isinstance(data.get("state"), str) or data.get("state") not in STATES or
                        not isinstance(data.get("message"), str) or len(data["message"]) > 1000):
                    await self._reply(writer, 400)
                    return
                self.jobs[job] = {"state": data["state"], "message": data["message"]}
                await self._reply(writer, 200)
            elif method in {"GET", "HEAD"} and leaf == "ControlIOS.tipa":
                writer.write((f"HTTP/1.1 200 OK\r\nContent-Length: {self.package.size}\r\n"
                              "Content-Type: application/octet-stream\r\nConnection: close\r\n\r\n").encode())
                await writer.drain()
                if method == "GET":
                    with self.package.path.open("rb") as source:
                        while chunk := source.read(256 * 1024):
                            writer.write(chunk)
                            await asyncio.wait_for(writer.drain(), 20)
                    self.hits[job] = self.hits.get(job, 0) + 1
            else:
                await self._reply(writer, 404)
        except (ValueError, UnicodeError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            try:
                await self._reply(writer, 400)
            except (OSError, asyncio.TimeoutError):
                pass
        finally:
            writer.close()
            self._handlers.discard(task)

    @staticmethod
    async def _reply(writer, code):
        writer.write(f"HTTP/1.1 {code} Response\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        await asyncio.wait_for(writer.drain(), 5)


async def vnc_ready(host: str, port: int) -> bool:
    """Require the initial RFB exchange, not just an open TCP port."""
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
        banner = await asyncio.wait_for(reader.readexactly(12), 3)
        if banner not in {b"RFB 003.008\n", b"RFB 003.007\n", b"RFB 003.003\n"}:
            return False
        writer.write(banner)
        await writer.drain()
        if banner == b"RFB 003.003\n":
            security = await asyncio.wait_for(reader.readexactly(4), 3)
            return int.from_bytes(security, "big") in {1, 2}
        count = (await asyncio.wait_for(reader.readexactly(1), 3))[0]
        if not count:
            return False
        await asyncio.wait_for(reader.readexactly(count), 3)
        return True
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        return False
    finally:
        if writer:
            writer.close()


async def update_one(channel, package: IOSPackage, server: UpdateServer,
                     vnc_port: int, on_event, *, timeout=420, interval=2,
                     verify_vnc=vnc_ready) -> str:
    address = ipaddress.ip_address(channel.host)
    if address.version != 4 or address.is_loopback:
        raise ControlError("Cập nhật qua LAN cần địa chỉ Wi-Fi IPv4 của iPhone")
    current = None
    for attempt in range(3):
        try:
            current = await asyncio.wait_for(channel.server_version(), 15)
            break
        except (UnauthorizedError, NotPatchedError):
            raise
        except (ControlError, asyncio.TimeoutError) as exc:
            if attempt == 2:
                raise ControlError(f"Không đọc được phiên bản trước cập nhật: {exc or 'hết thời gian chờ'}") from None
            on_event("Chưa đọc được phiên bản; đang kiểm tra lại kết nối…")
            await asyncio.sleep(interval)
    if version_key(current) >= version_key(package.version):
        if not await asyncio.wait_for(verify_vnc(channel.host, vnc_port), 12):
            raise ControlError(f"Đã có ControlIOS {current}; CTRIOS chưa phản hồi, không gửi lại gói cài")
        on_event(f"Đã có ControlIOS {current}, màn hình CTRIOS phản hồi; không cần cài lại")
        return "skipped"
    # Choose the PC interface that routes to THIS phone (VPN/multiple NIC safe).
    pc_ip = local_ip(channel.host)
    pc_address = ipaddress.ip_address(pc_ip)
    if not pc_address.is_private or pc_address.is_loopback:
        raise ControlError("Không tìm được địa chỉ LAN của PC để iPhone tải gói")
    native = version_key(current) >= version_key("4.18")
    if native:
        try:
            if (await asyncio.wait_for(channel.command("updateios check"), 15)).strip() != "OK LAN_UPDATE_1":
                raise ControlError("Máy chưa sẵn sàng cập nhật qua LAN")
        except (UnauthorizedError, NotPatchedError):
            raise
        except ControlError as exc:
            # Only an explicit preflight refusal permits the TrollStore route.
            # No install has been sent at this point; never retry an ambiguous dispatch.
            if not str(exc).startswith("ERR LocalUpdateUnavailable "):
                raise
            native = False
            on_event("Bộ cập nhật tự động chưa hoạt động; chuyển sang TrollStore để nâng cấp bản sửa lỗi")
    if not native and not await asyncio.wait_for(channel.find_trollstore(), 15):
        raise ControlError("Không tìm thấy TrollStore trên iPhone")
    job = server.add_job()
    url = server.url_for(f"{job}/ControlIOS.tipa", pc_ip)
    on_event(f"{current} → {package.version}: gửi gói từ PC qua LAN")
    try:
        if native:
            # An ambiguous/disconnected reply must never trigger a second install.
            reply = await asyncio.wait_for(channel.command(f"updateios {job} {package.version} {package.sha256} {url}"), 15)
            if reply.strip() != f"OK {job}":
                raise ControlError("Phản hồi cập nhật không hợp lệ")
        else:
            on_event("Nâng cấp lần đầu: nếu TrollStore hỏi hãy bấm Install; sau khi cài mở ControlIOS một lần nếu máy chưa tự kết nối lại")
            await asyncio.wait_for(channel.install_ipa(url), 30)
    except (UnauthorizedError, NotPatchedError):
        raise
    except (ControlError, asyncio.TimeoutError) as exc:
        if "ERR " in str(exc):
            raise
        on_event(f"Chưa nhận xác nhận lệnh: {exc or 'hết thời gian chờ'}; tiếp tục theo dõi, không gửi lại")
    return await wait_for_update(channel, package, server, job, vnc_port,
                                 on_event, current=current, timeout=timeout,
                                 interval=interval, verify_vnc=verify_vnc)


async def wait_for_update(channel, package, server, job, vnc_port, on_event,
                          *, current, timeout=420, interval=2, verify_vnc=vnc_ready):
    """Confirm the running service and RFB, with visible, bounded polling."""
    deadline = time.monotonic() + timeout
    last_status = None
    last_version = current
    last_reason = None
    next_progress = 0
    next_app_check = 0
    installed_version = None
    on_event("Đã gửi yêu cầu cài; đang xác nhận phiên bản dịch vụ và màn hình…")
    while time.monotonic() < deadline:
        status = server.jobs[job]
        state = status.get("state")
        if status != last_status:
            last_status = dict(status)
            if status:
                on_event(status["message"])
        if state == "error":
            raise ControlError(status["message"])
        reason = ""
        try:
            last_version = await asyncio.wait_for(channel.server_version(),
                min(15, max(.001, deadline - time.monotonic())))
            if version_key(last_version) >= version_key(package.version):
                reason = f"Dịch vụ đã lên {last_version}; đang chờ màn hình CTRIOS phản hồi"
                if await asyncio.wait_for(verify_vnc(channel.host, vnc_port),
                        min(12, max(.001, deadline - time.monotonic()))):
                    on_event(f"Thành công: ControlIOS {last_version}, màn hình CTRIOS phản hồi")
                    return "updated"
            else:
                reason = f"Dịch vụ vẫn là {last_version}; đang chờ ControlIOS {package.version} khởi động"
                # Legacy TrollStore offers no progress callbacks. Distinguish
                # its installed app from the daemon still answering commands.
                if time.monotonic() >= next_app_check:
                    next_app_check = time.monotonic() + 15
                    try:
                        apps = await asyncio.wait_for(channel.list_apps(),
                            min(10, max(.001, deadline - time.monotonic())))
                        installed_version = next((a.version for a in apps
                            if a.bundle_id == "com.controlios.app"), None)
                    except (UnauthorizedError, NotPatchedError):
                        raise
                    except (ControlError, asyncio.TimeoutError):
                        pass
                if installed_version and version_key(installed_version) >= version_key(package.version):
                    reason = f"App đã cài {installed_version}, dịch vụ còn {last_version}; mở ControlIOS trên iPhone để bật dịch vụ mới"
        except (UnauthorizedError, NotPatchedError):
            raise
        except asyncio.TimeoutError:
            reason = reason or "Đang chờ dịch vụ kết nối lại: hết thời gian kiểm tra"
        except (ControlError, ValueError) as exc:
            reason = f"Đang chờ dịch vụ kết nối lại: {exc or 'hết thời gian kiểm tra'}"
        if reason != last_reason or time.monotonic() >= next_progress:
            remaining = max(0, int(deadline - time.monotonic()))
            on_event(f"{reason} (còn tối đa {remaining}s)")
            last_reason = reason
            next_progress = time.monotonic() + 10
        await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
    downloaded = server.hits.get(job, 0) > 0
    if version_key(last_version) >= version_key(package.version):
        detail = f"Dịch vụ đã lên {last_version}, nhưng CTRIOS chưa phản hồi"
    elif installed_version and version_key(installed_version) >= version_key(package.version):
        detail = f"App đã cài {installed_version}; mở ControlIOS trên iPhone để bật dịch vụ mới"
    else:
        detail = ("Đã tải gói nhưng chưa xác nhận được phiên bản mới/màn hình; "
              "kiểm tra TrollStore và mở ControlIOS" if downloaded else
              f"Chưa tải được gói từ PC; kiểm tra LAN/tường lửa cổng {server.port}")
    raise ControlError(f"Hết thời gian chờ. {last_reason or detail}. Phiên bản phản hồi cuối: {last_version}. {detail}")
