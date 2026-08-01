"""Kênh điều khiển thứ hai: nói chuyện với control socket của TrollVNC.

Đây là kênh **song song** với VNC, không thay thế. VNC cho hình ảnh và
chuột/phím; kênh này cho những thứ VNC không làm được: liệt kê app đã cài, mở
và đóng app theo bundle id.

Giao thức của TrollVNC rất đơn giản: mỗi kết nối TCP phục vụ **đúng một dòng
lệnh** rồi đóng. Nên ở đây không có phiên nào phải giữ — mỗi lệnh là một kết
nối ngắn. Kết nối từ ngoài máy phải có tiền tố ``auth <token> ``.

Chỉ dùng được với bản TrollVNC đã vá (xem docs/trollvnc-patch.md).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

DEFAULT_CONTROL_PORT = 46752

log = logging.getLogger(__name__)


class ControlError(RuntimeError):
    """Máy trả về lỗi, hoặc không nói chuyện được."""


class NotPatchedError(ControlError):
    """Máy chạy TrollVNC gốc, chưa có các lệnh quản lý app."""


class UnauthorizedError(ControlError):
    """Sai token."""


@dataclass(frozen=True)
class AppInfo:
    bundle_id: str
    name: str
    kind: str          # "User" hoặc "System"
    version: str

    @property
    def is_user_app(self) -> bool:
        return self.kind.lower() == "user"

    @property
    def display_name(self) -> str:
        return self.name or self.bundle_id


@dataclass
class ControlChannel:
    """Một máy. Không giữ kết nối — mỗi lệnh mở một socket ngắn."""

    host: str
    port: int = DEFAULT_CONTROL_PORT
    token: str = ""
    timeout: float = 6.0

    async def command(self, line: str) -> str:
        """Gửi một lệnh, trả về nguyên văn phần trả lời."""

        payload = f"auth {self.token} {line}\n" if self.token else f"{line}\n"
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ControlError(
                f"{self.host}:{self.port} không phản hồi — TrollVNC chưa chạy, "
                f"hoặc bản trên máy chưa được vá ({exc})"
            ) from None

        try:
            writer.write(payload.encode("utf-8"))
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), timeout=self.timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise ControlError(f"{self.host}: mất kết nối giữa chừng ({exc})") from None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        text = data.decode("utf-8", errors="replace")
        self._raise_for_error(text, line)
        return text

    @staticmethod
    def _raise_for_error(text: str, line: str) -> None:
        head = text.strip()
        if head.startswith("ERR Unauthorized"):
            raise UnauthorizedError(
                "Sai token. Kiểm tra secret TVNC_CTL_TOKEN dùng lúc build máy đó."
            )
        if head.startswith("ERR Unknown"):
            raise NotPatchedError(
                f"Máy không hiểu lệnh {line.split()[0]!r} — nhiều khả năng đang chạy "
                "bản TrollVNC gốc, chưa cài bản đã vá."
            )
        if head.startswith("ERR Unavailable"):
            raise ControlError("Máy không truy cập được danh sách app (LSApplicationWorkspace).")
        if head.startswith("ERR "):
            raise ControlError(head)

    # ------------------------------------------------------------------ lệnh

    async def list_apps(self, user_only: bool = False) -> List[AppInfo]:
        text = await self.command("apps")
        apps = []
        for row in text.splitlines():
            if not row.strip():
                continue
            fields = row.split("\t")
            if len(fields) < 2:
                continue                      # dòng lạ, bỏ qua thay vì làm hỏng cả mẻ
            fields += [""] * (4 - len(fields))
            apps.append(AppInfo(fields[0], fields[1], fields[2], fields[3]))
        if user_only:
            apps = [a for a in apps if a.is_user_app]
        apps.sort(key=lambda a: (not a.is_user_app, a.display_name.lower()))
        return apps

    async def launch(self, bundle_id: str) -> None:
        text = await self.command(f"launch {bundle_id}")
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không mở được {bundle_id}: {text.strip()}")

    async def terminate(self, bundle_id: str) -> bool:
        """True nếu đã đóng, False nếu app vốn không chạy."""

        text = await self.command(f"terminate {bundle_id}")
        head = text.strip()
        if head.startswith("OK"):
            return True
        if head.startswith("NOT_RUNNING"):
            return False
        raise ControlError(f"Không đóng được {bundle_id}: {head}")

    async def put_file(self, local: Path | str, remote: str,
                       progress=None) -> int:
        """Đẩy một file lên máy. Trả về số byte máy xác nhận đã ghi.

        Gửi dòng tiêu đề ``put <size> <path>`` rồi đổ thẳng nội dung file, nên
        không tốn RAM cho file lớn và không phải mã hoá base64.
        """

        local = Path(local)
        size = local.stat().st_size

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ControlError(f"{self.host}:{self.port} không phản hồi ({exc})") from None

        try:
            header = f"auth {self.token} put {size} {remote}\n" if self.token \
                else f"put {size} {remote}\n"
            writer.write(header.encode("utf-8"))
            await writer.drain()

            sent = 0
            with local.open("rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    sent += len(chunk)
                    if progress:
                        progress(sent, size)

            # Máy chỉ trả lời sau khi nhận đủ, nên chờ rộng tay hơn lệnh thường.
            data = await asyncio.wait_for(reader.read(), timeout=max(self.timeout, 60))
        except (OSError, asyncio.TimeoutError) as exc:
            raise ControlError(f"{self.host}: đứt khi truyền file ({exc})") from None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        text = data.decode("utf-8", errors="replace")
        self._raise_for_error(text, "put")
        head = text.strip()
        if not head.startswith("OK"):
            raise ControlError(f"Máy từ chối file: {head}")
        parts = head.split()
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else size

    async def container(self, bundle_id: str) -> tuple[str, str]:
        """Trả về (thư mục dữ liệu, thư mục bundle) của một app.

        `/var/mobile/Documents/` là thư mục thật nhưng app Tệp của iOS không
        hiện nó — Tệp chỉ hiện container của app. Muốn file nhìn thấy được thì
        phải ghi vào đúng container.
        """

        text = await self.command(f"container {bundle_id}")
        head = text.strip()
        if head.startswith("NOT_FOUND"):
            raise ControlError(f"Máy không có app {bundle_id}")
        fields = head.split("\t")
        if not fields or not fields[0]:
            raise ControlError(f"Trả lời lạ cho lệnh container: {head!r}")
        return fields[0], (fields[1] if len(fields) > 1 else "")

    async def list_dir(self, path: str) -> List[tuple]:
        """Liệt kê thư mục: danh sách (tên, cỡ byte, có phải thư mục không)."""

        text = await self.command(f"ls {path}")
        entries = []
        for row in text.splitlines():
            fields = row.split("\t")
            if len(fields) < 3:
                continue
            entries.append((fields[0], int(fields[1] or 0), fields[2] == "1"))
        return entries

    async def open_url(self, url: str) -> None:
        text = await self.command(f"openurl {url}")
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không mở được URL: {text.strip()}")

    async def open_url_in(self, bundle_id: str, url: str) -> None:
        """Mở URL bằng **đúng app đó**, bỏ qua bước hệ thống tự chọn."""

        text = await self.command(f"openurlin {bundle_id} {url}")
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không mở được URL bằng {bundle_id}: {text.strip()}")

    async def find_trollstore(self) -> Optional[str]:
        """Bundle id của TrollStore trên chính máy đó.

        Hỏi máy thay vì viết cứng: bản build khác nhau có thể khác bundle id.
        """

        for app in await self.list_apps():
            if "trollstore" in app.bundle_id.lower() or \
                    app.display_name.strip().lower() == "trollstore":
                return app.bundle_id
        return None

    async def install_ipa(self, url: str) -> None:
        """Nhờ TrollStore trên máy tải và cài .ipa từ URL.

        Tự cài bằng installd cần bộ quyền mà TrollVNC không có; TrollStore mới
        là thứ làm việc này đúng cách, nên ta chỉ đưa URL cho nó.

        Phải chỉ đích danh TrollStore: `apple-magnifier://` là scheme TrollStore
        chiếm lại của app Kính lúp, và khi để hệ thống tự chọn thì nó chọn app
        Kính lúp gốc rồi bật camera.
        """

        target = f"apple-magnifier://install?url={quote(url, safe=':/?=&')}"
        bundle_id = await self.find_trollstore()
        if bundle_id:
            await self.open_url_in(bundle_id, target)
            return
        log.warning("%s: không thấy TrollStore, thử mở theo cách thường", self.host)
        await self.open_url(target)

    async def client_count(self) -> int:
        """Số client VNC đang nối vào máy — lệnh có sẵn của TrollVNC gốc."""

        text = await self.command("count")
        try:
            return int(text.strip())
        except ValueError:
            raise ControlError(f"Trả lời lạ cho lệnh count: {text.strip()!r}") from None


async def probe(host: str, port: int = DEFAULT_CONTROL_PORT, token: str = "",
                timeout: float = 3.0) -> Optional[str]:
    """Thử xem máy có control socket đã vá không.

    Trả về None nếu dùng được, hoặc câu mô tả vì sao không.
    """

    channel = ControlChannel(host, port, token, timeout)
    try:
        await channel.command("apps")
        return None
    except ControlError as exc:
        return str(exc)
