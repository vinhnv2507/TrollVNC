"""Bật SSH bằng khoá trên máy — thoát bế tắc mật khẩu của Dopamine.

Quy trình một lệnh:
1. Sinh (nếu chưa có) một cặp khoá SSH cho Control IOS.
2. Cài khoá công khai vào máy **qua control socket của TrollVNC** — kênh đó
   chạy bằng root nên ghi được `/var/root/.ssh/authorized_keys`.
3. Thử đăng nhập SSH bằng khoá để xác nhận.

Sau bước này, đăng nhập SSH không cần mật khẩu, và làm được cho cả 250 máy mà
không phải chạm tay từng cái.

    python -m controlios.ssh_setup 172.30.0.221
    python -m controlios.ssh_setup 172.30.0.221 172.30.3.152 ...
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import DEFAULT_REGISTRY, Registry
from .control_channel import ControlChannel, ControlError
from .ssh_channel import SshChannel, SshError
from .sshkey import ensure_keypair


async def setup_one(host: str, settings, public_key: str, key_path: Path,
                    user: str) -> tuple[str, bool, str]:
    control = ControlChannel(host, settings.control_port, settings.control_token)
    try:
        remote = await control.install_ssh_key(public_key, user=user)
    except ControlError as exc:
        return host, False, f"cài khoá thất bại (TrollVNC đã vá chưa?): {exc}"

    ssh = SshChannel(host, settings.ssh_port, user, key_path=str(key_path))
    try:
        result = await ssh.run("id")
    except SshError as exc:
        return host, False, f"đã ghi {remote} nhưng SSH chưa vào được: {exc}"

    return host, True, result.output


async def run(hosts: list[str], user: str) -> int:
    settings = Registry.load(DEFAULT_REGISTRY).settings
    if not settings.control_token:
        print("Chưa có control_token trong config/devices.json", file=sys.stderr)
        return 2

    key_path, public_key = ensure_keypair()
    print(f"Khoá: {key_path}")
    print(f"Cài khoá công khai cho tài khoản '{user}' trên {len(hosts)} máy…\n")

    results = await asyncio.gather(
        *(setup_one(h, settings, public_key, key_path, user) for h in hosts)
    )

    ok = 0
    for host, success, detail in results:
        print(f"  {'✓' if success else '✗'} {host}: {detail}")
        ok += success

    print(f"\nXong {ok}/{len(hosts)} máy.")
    if ok:
        print("\nĐã lưu đường dẫn khoá vào config/devices.json — SSH giờ dùng khoá.")
        registry = Registry.load(DEFAULT_REGISTRY)
        registry.settings.ssh_key_path = str(key_path)
        registry.settings.ssh_user = user
        registry.save(DEFAULT_REGISTRY)
    return 0 if ok == len(hosts) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="controlios.ssh_setup")
    parser.add_argument("hosts", nargs="+", help="IP các máy")
    parser.add_argument("--user", default="root", choices=["root", "mobile"],
                        help="tài khoản để cài khoá (mặc định root)")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.hosts, args.user))


if __name__ == "__main__":
    sys.exit(main())
