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
from typing import List, Optional

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
