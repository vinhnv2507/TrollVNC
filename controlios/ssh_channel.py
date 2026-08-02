"""Kênh thứ ba: SSH tới máy đã jailbreak.

Đây là kênh **có sức mạnh lớn nhất** trong ba kênh, và nó chấm dứt vòng lặp
"vá TrollVNC → build trên GitHub → cài lại từng máy": mọi tính năng mới sau này
chỉ còn là một câu lệnh shell.

Ba kênh bổ nhau, không thay thế nhau:

* **VNC** — hình ảnh và thao tác chuột/phím, chạy trên mọi máy
* **Control socket** — app, truyền file; cần TrollVNC đã vá
* **SSH** (file này) — lệnh tuỳ ý; cần máy đã jailbreak

Máy chưa jailbreak sẽ báo :class:`SshUnavailable` rõ ràng chứ không treo.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USER = "root"


class SshError(RuntimeError):
    """Lệnh chạy được nhưng thất bại, hoặc kết nối hỏng giữa chừng."""


class SshUnavailable(SshError):
    """Máy không mở SSH — nhiều khả năng chưa jailbreak."""


class SshAuthError(SshError):
    """Sai tài khoản hoặc mật khẩu."""


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """Phần đáng đọc: stdout, hoặc stderr nếu lệnh hỏng."""
        return (self.stdout if self.ok else (self.stderr or self.stdout)).strip()

    def check(self) -> "CommandResult":
        if not self.ok:
            raise SshError(f"[{self.exit_code}] {self.command}: {self.output}")
        return self


@dataclass
class SshChannel:
    """Một máy. Mỗi lệnh mở một kết nối ngắn, giống control socket.

    Không giữ kết nối lâu vì với 250 máy thì số socket mở thường trực là gánh
    nặng không cần thiết — lệnh SSH vốn thưa, khác hẳn luồng hình VNC.
    """

    host: str
    port: int = DEFAULT_SSH_PORT
    username: str = DEFAULT_SSH_USER
    password: str = ""
    timeout: float = 12.0

    def _connect(self):
        import asyncssh

        return asyncssh.connect(
            self.host, self.port,
            username=self.username,
            password=self.password or None,
            # Máy trong mạng nội bộ, và khoá máy đổi mỗi lần cài lại jailbreak
            # nên kiểm tra known_hosts chỉ gây phiền chứ không thêm an toàn.
            known_hosts=None,
            connect_timeout=self.timeout,
        )

    async def _open(self):
        import asyncssh

        try:
            return await asyncio.wait_for(self._connect(), timeout=self.timeout)
        except asyncssh.PermissionDenied as exc:
            raise SshAuthError(
                f"{self.host}: sai tài khoản/mật khẩu SSH ({self.username})"
            ) from None
        except (OSError, asyncio.TimeoutError, asyncssh.Error) as exc:
            raise SshUnavailable(
                f"{self.host}:{self.port} không mở SSH — máy chưa jailbreak, "
                f"chưa cài OpenSSH, hoặc đã khởi động lại và mất jailbreak ({exc})"
            ) from None

    async def run(self, command: str) -> CommandResult:
        async with await self._open() as conn:
            try:
                result = await asyncio.wait_for(
                    conn.run(command, check=False), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                raise SshError(f"{self.host}: lệnh quá lâu: {command}") from None

        return CommandResult(
            command=command,
            exit_code=result.exit_status if result.exit_status is not None else -1,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )

    async def run_all(self, commands: List[str]) -> List[CommandResult]:
        """Nhiều lệnh trên **một** kết nối — nhanh hơn hẳn mở lại từng lần."""

        results = []
        async with await self._open() as conn:
            for command in commands:
                result = await asyncio.wait_for(
                    conn.run(command, check=False), timeout=self.timeout
                )
                results.append(CommandResult(
                    command=command,
                    exit_code=result.exit_status if result.exit_status is not None else -1,
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                ))
        return results

    async def is_available(self) -> bool:
        """Máy có SSH không — dùng để biết máy còn jailbreak hay đã reboot."""

        try:
            await self.run("true")
            return True
        except SshError:
            return False

    # ------------------------------------------------------------------ file

    async def upload(self, local: Path | str, remote: str) -> None:
        import asyncssh

        async with await self._open() as conn:
            try:
                async with conn.start_sftp_client() as sftp:
                    await sftp.put(str(local), remote)
            except (OSError, asyncssh.Error) as exc:
                raise SshError(f"{self.host}: không đẩy được file ({exc})") from None

    async def download(self, remote: str, local: Path | str) -> None:
        import asyncssh

        async with await self._open() as conn:
            try:
                async with conn.start_sftp_client() as sftp:
                    await sftp.get(remote, str(local))
            except (OSError, asyncssh.Error) as exc:
                raise SshError(f"{self.host}: không lấy được file ({exc})") from None
