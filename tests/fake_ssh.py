"""Server SSH giả chạy trong tiến trình, để test không cần iPhone jailbreak.

Nó nói giao thức SSH thật (qua asyncssh) chứ không phải giả lập nửa vời, nên
những gì test được ở đây phản ánh đúng hành vi của client.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import asyncssh

# Khoá dùng một lần cho test; sinh lúc chạy nên không có bí mật nào trong repo.
_HOST_KEY = asyncssh.generate_private_key("ssh-rsa", key_size=2048)


@dataclass
class FakeSshServer:
    username: str = "root"
    password: str = "alpine"
    #: các khoá công khai (dạng OpenSSH) được chấp nhận cho đăng nhập bằng khoá
    authorized_keys: List[str] = field(default_factory=list)

    #: lệnh -> (mã trả về, stdout, stderr). Lệnh không khai thì trả về mã 127.
    responses: Dict[str, tuple] = field(default_factory=dict)
    #: mọi lệnh đã nhận, theo thứ tự
    commands: List[str] = field(default_factory=list)
    #: file đã nhận qua SFTP
    received: Dict[str, bytes] = field(default_factory=dict)

    #: đặt True để giả lập máy chưa jailbreak (từ chối kết nối)
    refuse: bool = False

    _server: Optional[asyncssh.SSHAcceptor] = None
    port: int = 0
    _root: Optional[Path] = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        outer = self

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username: str) -> bool:
                return True     # bắt buộc xác thực

            def password_auth_supported(self) -> bool:
                return True

            def validate_password(self, username: str, password: str) -> bool:
                return username == outer.username and password == outer.password

            def public_key_auth_supported(self) -> bool:
                return bool(outer.authorized_keys)

            def validate_public_key(self, username, key) -> bool:
                for authorized in outer.authorized_keys:
                    try:
                        if key == asyncssh.import_public_key(authorized):
                            return True
                    except (ValueError, asyncssh.KeyImportError):
                        continue
                return False

        async def handle(process: asyncssh.SSHServerProcess) -> None:
            command = process.command or ""
            outer.commands.append(command)
            code, out, err = outer.responses.get(command, (127, "", f"{command}: not found"))
            if out:
                process.stdout.write(out)
            if err:
                process.stderr.write(err)
            process.exit(code)

        self._server = await asyncssh.listen(
            host, port,
            server_factory=Server,
            server_host_keys=[_HOST_KEY],
            process_factory=handle,
            sftp_factory=True,
            allow_scp=True,
        )
        self.port = self._server.get_addresses()[0][1]
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self._server = None

    def expect(self, command: str, stdout: str = "", code: int = 0,
               stderr: str = "") -> None:
        self.responses[command] = (code, stdout, stderr)
