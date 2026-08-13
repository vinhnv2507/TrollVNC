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
import base64
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


@dataclass(frozen=True)
class Snapshot:
    """Một bản snapshot dữ liệu app trên máy."""

    name: str
    epoch: int          # thời điểm lưu (giây Unix), 0 nếu không rõ
    size: int           # tổng cỡ byte

    @property
    def size_mb(self) -> float:
        return self.size / (1024 * 1024)


@dataclass
class ControlChannel:
    """Một máy. Không giữ kết nối — mỗi lệnh mở một socket ngắn."""

    host: str
    port: int = DEFAULT_CONTROL_PORT
    token: str = ""
    timeout: float = 6.0
    # Qua USB, relay nối tới control socket ở **loopback trên máy**, mà server chỉ
    # cắt tiền tố `auth <token>` cho kết nối NGOÀI loopback. Vậy với đường USB
    # phải gửi lệnh **không kèm auth**, nếu không server hiểu nhầm `auth` là lệnh.
    loopback: bool = False

    def _auth_prefix(self) -> str:
        if self.loopback or not self.token:
            return ""
        return f"auth {self.token} "

    async def command(self, line: str, read_timeout: Optional[float] = None) -> str:
        """Gửi một lệnh, trả về nguyên văn phần trả lời.

        ``read_timeout`` nới thời gian CHỜ TRẢ LỜI cho các lệnh chạy lâu (chép dữ
        liệu app lớn); mặc định dùng ``self.timeout``.
        """

        payload = f"{self._auth_prefix()}{line}\n"
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
            data = await asyncio.wait_for(
                reader.read(), timeout=read_timeout or self.timeout)
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

    async def wake_if_locked(self) -> bool:
        """Bấm Home nếu màn hình iOS đang khóa/tắt."""

        text = (await self.command("wakeiflocked")).strip().lower()
        if text == "ok home":
            return True
        if text == "ok unlocked":
            return False
        raise ControlError(f"Phản hồi trạng thái khóa không hợp lệ: {text}")

    async def terminate(self, bundle_id: str) -> bool:
        """True nếu đã đóng, False nếu app vốn không chạy."""

        text = await self.command(f"terminate {bundle_id}")
        head = text.strip()
        if head.startswith("OK"):
            return True
        if head.startswith("NOT_RUNNING"):
            return False
        raise ControlError(f"Không đóng được {bundle_id}: {head}")

    async def wipe_app(self, bundle_id: str) -> None:
        """Xoá dữ liệu app (Documents/Library/tmp/SystemData) như vừa cài lại.

        GIỮ container và KHÔNG đụng keychain — token/khoá trong keychain vẫn còn,
        nên đây là "clear data" ở mức file, không phải "máy chưa từng cài". Nên
        :meth:`terminate` app trước khi gọi.
        """

        text = await self.command(f"wipeapp {bundle_id}", read_timeout=180)
        head = text.strip()
        if head.startswith("NOT_FOUND"):
            raise ControlError(f"Máy không có app {bundle_id}")
        if not head.startswith("OK"):
            raise ControlError(f"Không xoá được dữ liệu {bundle_id}: {head}")

    async def snapshot_app(self, bundle_id: str, name: str = "") -> str:
        """Lưu một bản snapshot dữ liệu app NGAY TRÊN MÁY. Trả về tên bản đã lưu.

        Bỏ trống ``name`` thì máy tự đặt tên theo thời gian (``yyyyMMdd-HHmmss``).
        Nhiều bản cùng lúc, mỗi tên một bản; cùng tên thì ghi đè. Dùng
        :meth:`list_snapshots` để liệt kê và :meth:`restore_app` để chọn bản.
        """

        line = f"snapshot {bundle_id}" + (f" {name}" if name else "")
        text = await self.command(line, read_timeout=180)
        head = text.strip()
        if head.startswith("NOT_FOUND"):
            raise ControlError(f"Máy không có app {bundle_id}")
        if head.startswith("ERR BadName"):
            raise ControlError("Tên snapshot không hợp lệ (không dùng '/', '..' hay dấu cách).")
        if not head.startswith("OK"):
            raise ControlError(f"Không lưu được snapshot {bundle_id}: {head}")
        parts = head.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else name

    async def list_snapshots(self, bundle_id: str) -> List["Snapshot"]:
        """Danh sách các bản snapshot của một app (tên, thời điểm, cỡ). Rỗng nếu
        chưa có bản nào. Mới nhất lên đầu."""

        text = await self.command(f"snaplist {bundle_id}")
        self._raise_for_error(text, "snaplist")
        snaps = []
        for row in text.splitlines():
            if not row.strip():
                continue
            fields = row.split("\t")
            if not fields[0]:
                continue
            epoch = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0
            size = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else 0
            snaps.append(Snapshot(fields[0], epoch, size))
        snaps.sort(key=lambda s: s.epoch, reverse=True)
        return snaps

    async def restore_app(self, bundle_id: str, name: str) -> None:
        """Thay dữ liệu app hiện tại bằng đúng bản snapshot ``name``. Nên
        :meth:`terminate` trước, rồi mở lại app sau."""

        if not name:
            raise ValueError("restore cần tên snapshot")
        text = await self.command(f"restore {bundle_id} {name}", read_timeout=180)
        head = text.strip()
        if head.startswith("NOT_FOUND"):
            raise ControlError(f"Máy không có app {bundle_id}")
        if head.startswith("ERR NoSnapshot"):
            raise ControlError(f"Máy không có snapshot tên {name!r} cho {bundle_id}.")
        if not head.startswith("OK"):
            raise ControlError(f"Không khôi phục được {bundle_id}: {head}")

    async def delete_snapshot(self, bundle_id: str, name: str) -> None:
        """Xoá một bản snapshot."""

        if not name:
            raise ValueError("snapdel cần tên snapshot")
        text = await self.command(f"snapdel {bundle_id} {name}")
        head = text.strip()
        if head.startswith("NOT_FOUND"):
            raise ControlError(f"Máy không có snapshot tên {name!r} cho {bundle_id}.")
        if not head.startswith("OK"):
            raise ControlError(f"Không xoá được snapshot {name!r}: {head}")

    async def clear_snapshots(self, bundle_id: str) -> None:
        """Xoá TẤT CẢ snapshot của một app (dọn luôn rác của bản cũ nếu còn)."""

        text = await self.command(f"snapclear {bundle_id}", read_timeout=60)
        head = text.strip()
        if not head.startswith("OK"):
            raise ControlError(f"Không xoá được snapshot của {bundle_id}: {head}")

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
            header = f"{self._auth_prefix()}put {size} {remote}\n"
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

    async def get_file(self, remote: str, local: Path | str, progress=None) -> int:
        """Tải một file snapshot qua control socket, không cần SSH."""

        local = Path(local)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise ControlError(f"{self.host}:{self.port} không phản hồi ({exc})") from None
        try:
            writer.write(f"{self._auth_prefix()}getfile {remote}\n".encode("utf-8"))
            await writer.drain()
            header_raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            header = header_raw.decode("utf-8", errors="replace").strip()
            self._raise_for_error(header, "getfile")
            fields = header.split()
            if len(fields) != 2 or fields[0] != "OK" or not fields[1].isdigit():
                raise ControlError(f"Trả lời lạ cho getfile: {header!r}")
            size = int(fields[1])
            local.parent.mkdir(parents=True, exist_ok=True)
            received = 0
            with local.open("wb") as handle:
                while received < size:
                    chunk = await asyncio.wait_for(
                        reader.read(min(64 * 1024, size - received)),
                        timeout=max(self.timeout, 60))
                    if not chunk:
                        raise ControlError(
                            f"{self.host}: file bị ngắt giữa chừng ({received}/{size} byte)")
                    handle.write(chunk)
                    received += len(chunk)
                    if progress:
                        progress(received, size)
            return received
        except Exception:
            local.unlink(missing_ok=True)
            raise
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def download_tree(self, remote: str, local: Path | str) -> int:
        """Tải đệ quy một thư mục snapshot; trả tổng số byte."""

        local = Path(local)
        local.mkdir(parents=True, exist_ok=True)
        total = 0
        for name, _size, is_dir in await self.list_dir(remote):
            if not name or name in (".", "..") or "/" in name or "\\" in name:
                continue
            child_remote = remote.rstrip("/") + "/" + name
            child_local = local / name
            if is_dir:
                total += await self.download_tree(child_remote, child_local)
            else:
                total += await self.get_file(child_remote, child_local)
        return total

    async def install_ssh_key(self, public_key: str, user: str = "root") -> str:
        """Cài khoá công khai để đăng nhập SSH bằng khoá, không cần mật khẩu.

        Control socket chạy bằng **root**, nên nó ghi được vào
        `/var/root/.ssh/authorized_keys`. File đó do root sở hữu — đúng thứ sshd
        cần cho việc đăng nhập bằng chính tài khoản root. Đây là đường thoát khi
        tài khoản bị khoá và không đặt được mật khẩu (thường gặp trên Dopamine).

        Trả về đường dẫn authorized_keys trên máy.
        """

        home = "/var/root" if user == "root" else f"/var/mobile"
        remote = f"{home}/.ssh/authorized_keys"

        data = public_key.strip() + "\n"
        import tempfile
        tmp = Path(tempfile.gettempdir()) / "controlios_authkey.pub"
        tmp.write_text(data, encoding="ascii")
        try:
            await self.put_file(tmp, remote)
        finally:
            tmp.unlink(missing_ok=True)
        return remote

    async def _exchange(self, header_line: str, payload: bytes = b"",
                        read_timeout: Optional[float] = None) -> bytes:
        """Gửi một dòng lệnh (kèm payload nhị phân tuỳ chọn), trả về byte thô.

        Dùng cho các lệnh có phần dữ liệu đi liền sau dòng lệnh (``clipset``) hoặc
        có phần trả lời nhị phân (``clipget``) — nơi mà ``command`` giải mã sẵn
        thành chuỗi là không đủ.
        """

        header = f"{self._auth_prefix()}{header_line}\n".encode("utf-8")
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
            writer.write(header)
            if payload:
                writer.write(payload)
            await writer.drain()
            data = await asyncio.wait_for(
                reader.read(), timeout=read_timeout or self.timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ControlError(f"{self.host}: mất kết nối giữa chừng ({exc})") from None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        return data

    async def set_clipboard(self, text: str) -> int:
        """Đặt clipboard của máy = ``text`` (UTF-8). Trả về số byte máy xác nhận.

        Đây là đường vòng qua giới hạn clipboard latin-1 của client VNC: chữ đi
        thẳng vào ``UIPasteboard`` nên **dán được khối chữ dài, đủ dấu tiếng
        Việt** cho hàng loạt máy mà không phải gõ từng ký tự qua keysym.
        """

        payload = text.encode("utf-8")
        data = await self._exchange(f"clipset {len(payload)}", payload)
        reply = data.decode("utf-8", errors="replace")
        self._raise_for_error(reply, "clipset")
        head = reply.strip()
        if not head.startswith("OK"):
            raise ControlError(f"Máy từ chối clipboard: {head}")
        parts = head.split()
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else len(payload)

    async def get_clipboard(self) -> str:
        """Đọc clipboard hiện tại của máy (UTF-8)."""

        data = await self._exchange("clipget")
        self._raise_for_error(data.decode("utf-8", errors="replace"), "clipget")
        newline = data.find(b"\n")
        if newline == -1:
            raise ControlError(f"Trả lời lạ cho clipget: {data!r}")
        header = data[:newline].decode("utf-8", errors="replace").strip()
        parts = header.split()
        if not parts or parts[0] != "OK":
            raise ControlError(f"Trả lời lạ cho clipget: {header!r}")
        size = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        payload = data[newline + 1:newline + 1 + size]
        return payload.decode("utf-8", errors="replace")

    async def save_photo(self, remote_path: str) -> None:
        """Nạp một file ảnh **đã có trên máy** vào Thư viện Ảnh qua PHPhotoLibrary.

        Chép ảnh vào thư mục thường không làm nó hiện trong app Ảnh — iOS quản
        ảnh bằng cơ sở dữ liệu riêng. Lệnh này gọi ``PHAssetCreationRequest`` để
        ảnh dùng được trong Shopee/TikTok. Ghép với :meth:`put_file` thì thành
        đẩy-ảnh-rồi-nạp trong một lượt (xem :meth:`push_photo`).
        """

        # Nạp ảnh có thể lâu hơn một dòng lệnh: PHPhotoLibrary ghi bất đồng bộ.
        data = await self._exchange(
            f"savephoto {remote_path}", read_timeout=max(self.timeout, 30)
        )
        text = data.decode("utf-8", errors="replace")
        self._raise_for_error(text, "savephoto")
        head = text.strip()
        if not head.startswith("OK"):
            raise ControlError(f"Không nạp được ảnh vào Thư viện: {head}")

    async def push_photo(self, local: Path | str,
                         remote_dir: str = "/var/mobile/Media/controlios",
                         normalize: bool = True) -> None:
        """Đẩy một ảnh/video từ PC rồi nạp thẳng vào Thư viện Ảnh của máy.

        ``normalize=True`` (mặc định): video lạ định dạng được tự re-encode sang
        chuẩn iOS trước khi đẩy (cần ffmpeg). Tầng pool tắt cờ này vì đã chuẩn
        hoá một lần cho cả mẻ.
        """

        local = Path(local)
        if normalize:
            from . import media
            local = await asyncio.to_thread(media.ensure_ios_media, local)
        remote = f"{remote_dir.rstrip('/')}/{local.name}"
        await self.put_file(local, remote)
        await self.save_photo(remote)

    async def respring(self) -> None:
        """Khởi động lại SpringBoard — gỡ giao diện treo, KHÔNG mất jailbreak."""

        text = await self.command("respring")
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không respring được: {text.strip()}")

    async def set_assistive_touch(self, state: str) -> None:
        """Bật/tắt AssistiveTouch của iOS. state = 'on' | 'off' | 'toggle'."""

        text = await self.command(f"assistivetouch {state}")
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không đổi được AssistiveTouch: {text.strip()}")

    async def reboot(self) -> None:
        """Khởi động lại toàn bộ thiết bị."""
        text = await self.command("reboot", read_timeout=5)
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không reboot được: {text.strip()}")

    async def shutdown(self) -> None:
        """Tắt nguồn toàn bộ thiết bị."""
        text = await self.command("shutdown", read_timeout=5)
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không tắt máy được: {text.strip()}")

    # ------------------------------------------------------- ControlIOSKeeper

    async def get_keeper_status(self) -> tuple[bool, bool]:
        """(keeperd đang chạy?, app Keeper đang chạy?) trên máy.

        keeperd là daemon canh ControlIOS: nó bind cổng 46753 ở loopback nên PC
        không dò trực tiếp được, phải hỏi qua control socket của ControlIOS."""

        text = await self.command("keeper status")
        parts = text.strip().split()
        if not parts or parts[0] != "OK":
            raise ControlError(f"Không đọc được trạng thái Keeper: {text.strip()}")
        flags = set(parts[1:])
        return "keeperd" in flags, "app" in flags

    async def start_keeper(self) -> str:
        """Bật keeperd trên máy nếu nó đang chết. Trả về mô tả việc đã làm.

        Daemon spawn thẳng keeperd từ bundle Keeper (không mở UI app) nên KHÔNG
        chiếm màn hình đang chạy việc của farm."""

        text = await self.command("keeper start", read_timeout=15)
        stripped = text.strip()
        if not stripped.startswith("OK"):
            raise ControlError(f"Không bật được Keeper: {stripped}")
        return stripped[len("OK"):].strip() or "đã bật"

    async def ensure_keeper(self) -> tuple[bool, str]:
        """Kiểm tra Keeper, bật lại nếu chết. Trả về (có phải bật lại?, mô tả)."""

        keeperd, _app = await self.get_keeper_status()
        if keeperd:
            return False, "keeperd đang chạy"
        note = await self.start_keeper()

        # posix_spawn chỉ xác nhận đã tạo process, chưa xác nhận daemon đã
        # bind được cổng 46753. Chờ ngắn và đọc status lại để không báo thành
        # công giả khi Keeper thiếu binary, entitlements hoặc không chạy root.
        for _ in range(10):
            await asyncio.sleep(0.2)
            keeperd, _app = await self.get_keeper_status()
            if keeperd:
                return True, note
        raise ControlError("Keeper đã được spawn nhưng keeperd chưa hoạt động")

    # --------------------------------------------- auto-click (JS chạy trên máy)

    async def push_autoscript(self, script: str) -> None:
        """Đẩy kịch bản JavaScript auto-click xuống máy (lưu, chưa chạy)."""

        b64 = base64.b64encode(script.encode("utf-8")).decode()
        text = await self.command(f"autoset {b64}", read_timeout=15)
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không đẩy được kịch bản: {text.strip()}")

    async def push_prelude(self, js: str) -> None:
        """Đẩy THƯ VIỆN HÀM (JS) xuống máy — nạp trước mọi kịch bản, không cần
        cài lại app. Dùng để thêm hàm tiện ích mới qua socket."""

        b64 = base64.b64encode(js.encode("utf-8")).decode()
        text = await self.command(f"setprelude {b64}", read_timeout=15)
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không đẩy được thư viện hàm: {text.strip()}")

    async def autoclick_start(self) -> None:
        """Bắt đầu chạy kịch bản auto-click đã đẩy."""

        text = await self.command("autostart")
        head = text.strip()
        if head.startswith("ERR NoScript"):
            raise ControlError("Chưa có kịch bản trên máy — đẩy trước khi chạy.")
        if not head.startswith("OK"):
            raise ControlError(f"Không chạy được auto-click: {head}")

    async def autoclick_stop(self) -> None:
        """Dừng auto-click."""

        text = await self.command("autostop")
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không dừng được auto-click: {text.strip()}")

    async def autoclick_status(self) -> bool:
        """True nếu auto-click đang chạy."""

        text = await self.command("autostatus")
        return "running" in text

    async def get_autolog(self) -> tuple[bool, str]:
        """(đang chạy?, nhật ký) của auto-click trên máy — để theo dõi tiến trình."""

        text = await self.command("autolog")
        parts = text.strip().split(maxsplit=2)
        running = len(parts) > 1 and parts[1] == "running"
        log = ""
        if len(parts) > 2:
            try:
                log = base64.b64decode(parts[2]).decode("utf-8", errors="replace")
            except Exception:
                log = ""
        return running, log

    async def clear_autolog(self) -> None:
        """Xoá nhật ký auto-click trên máy."""

        await self.command("autologclear")

    async def get_color(self, rx: float, ry: float) -> Optional[str]:
        """Đọc MÀU THẬT "RRGGBB" tại điểm tỉ lệ (rx, ry) trên máy — daemon lấy
        pixel gốc trên framebuffer (đúng cái getColor/matchColor auto-click dùng),
        chuẩn hơn đọc từ khung đã nén ở PC. None nếu máy chưa có khung/không hỗ trợ."""

        try:
            text = (await self.command(
                f"color {rx:.4f} {ry:.4f}", read_timeout=3.0)).strip()
        except NotPatchedError:
            return None          # bản TrollVNC cũ chưa có lệnh 'color'
        parts = text.split()
        if len(parts) >= 2 and parts[0] == "OK" and len(parts[1]) == 6:
            return parts[1].upper()
        return None

    async def set_scale(self, factor: float) -> None:
        """Đổi hệ số scale khung hình (0<factor<=1) lúc đang chạy.

        Khung nhỏ hơn -> máy nén nhanh hơn -> mượt hơn, đổi lại kém nét. Đổi kích
        thước làm phiên VNC nối lại một nhịp (như xoay máy). Cần TrollVNC đã vá.
        """

        if not (0.0 < factor <= 1.0):
            raise ValueError("scale phải trong khoảng (0, 1]")
        text = await self.command(f"setscale {factor:.3f}")
        if not text.strip().startswith("OK"):
            raise ControlError(f"Không đổi được scale: {text.strip()}")

    async def set_smoothness(self, inflight: int, defer: float,
                             orientation_sync: bool) -> None:
        """Chỉnh các tham số ĐỘ MƯỢT lúc chạy — KHÔNG resize nên không nối lại.

        - inflight: số khung tối đa đang mã hoá trước khi bỏ khung mới (Q). 1 =
          độ trễ thấp nhất (bỏ khung cũ). 2 = mượt hơn khi mạng ổn.
        - defer: cửa sổ gộp khung (giây). Nhỏ = trễ thấp; lớn = nhẹ CPU/băng thông.
        - orientation_sync: đồng bộ xoay. Tắt -> hết chớp đen khi app xoay.
        Bỏ qua lệnh nào máy chưa hỗ trợ (bản cũ) thay vì báo lỗi cả cụm.
        """

        for line in (f"setinflight {int(inflight)}",
                     f"setdefer {defer:.3f}",
                     f"setorient {'on' if orientation_sync else 'off'}"):
            try:
                await self.command(line)
            except NotPatchedError:
                pass  # bản TrollVNC cũ chưa có lệnh này

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
