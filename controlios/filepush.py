"""Đẩy một file lên máy từ dòng lệnh, để thử nhanh không cần mở giao diện.

    python -m controlios.filepush 172.30.0.221 README.md /var/mobile/Documents/thu.txt
    python -m controlios.filepush 172.30.0.221 app.ipa --install
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import DEFAULT_REGISTRY, Registry
from .control_channel import ControlChannel, ControlError
from .fileserver import FileServer, local_ip


async def _push(channel: ControlChannel, local: Path, remote: str) -> int:
    def progress(done: int, total: int) -> None:
        if total:
            print(f"\r  {done * 100 // total}% ({done}/{total} byte)", end="", flush=True)

    written = await channel.put_file(local, remote, progress=progress)
    print()
    return written


async def _install(channel: ControlChannel, ipa: Path, seconds: float) -> bool:
    server = FileServer()
    await server.start()
    try:
        name = server.add(ipa)
        url = server.url_for(name, local_ip())
        print(f"Phục vụ {name} tại {url}")
        await channel.install_ipa(url)
        print("Đã gửi lệnh cài. Chờ máy tải về…")

        for _ in range(int(seconds / 2)):
            await asyncio.sleep(2)
            if server.hits.get(name, 0) > 0:
                print("Máy đã tải xong file. Bấm xác nhận trên iPhone nếu TrollStore hỏi.")
                return True
        print("Hết giờ chờ mà máy chưa tải. Kiểm tra tường lửa Windows có chặn "
              f"cổng {server.port} không.")
        return False
    finally:
        await server.stop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="controlios.filepush")
    parser.add_argument("device", help="IP của máy")
    parser.add_argument("local", type=Path, help="file trên PC")
    parser.add_argument("remote", nargs="?", help="đường dẫn đích trên máy")
    parser.add_argument("--install", action="store_true",
                        help="phục vụ file qua HTTP rồi nhờ TrollStore cài .ipa")
    parser.add_argument("--app", metavar="BUNDLE_ID",
                        help="đẩy vào thư mục Documents của app này, thay vì "
                             "đường dẫn tuyệt đối (app Tệp chỉ thấy chỗ này)")
    parser.add_argument("--ls", metavar="PATH",
                        help="liệt kê một thư mục trên máy rồi thoát")
    parser.add_argument("--token", help="mặc định lấy từ config/devices.json")
    parser.add_argument("--port", type=int)
    parser.add_argument("--wait", type=float, default=120,
                        help="số giây chờ máy tải khi dùng --install")
    args = parser.parse_args(argv)

    settings = Registry.load(DEFAULT_REGISTRY).settings
    token = args.token or settings.control_token
    if not token:
        print("Chưa có control_token trong config/devices.json", file=sys.stderr)
        return 2

    channel = ControlChannel(args.device, args.port or settings.control_port, token,
                             timeout=10)

    if args.ls:
        try:
            for name, size, is_dir in asyncio.run(channel.list_dir(args.ls)):
                print(f"{'d' if is_dir else '-'} {size:>12}  {name}")
        except ControlError as exc:
            print(f"LỖI: {exc}", file=sys.stderr)
            return 1
        return 0

    if not args.local.is_file():
        print(f"Không thấy file {args.local}", file=sys.stderr)
        return 2

    remote = args.remote
    if args.app:
        try:
            data_dir, _bundle = asyncio.run(channel.container(args.app))
        except ControlError as exc:
            print(f"LỖI: {exc}", file=sys.stderr)
            return 1
        remote = f"{data_dir}/Documents/{args.local.name}"
        print(f"Container của {args.app}: {data_dir}")
        print(f"Đích: {remote}")

    if not args.install and not remote:
        print("Cần đường dẫn đích, hoặc --app <bundle id>, hoặc --install",
              file=sys.stderr)
        return 2

    try:
        if args.install:
            ok = asyncio.run(_install(channel, args.local, args.wait))
            return 0 if ok else 1
        written = asyncio.run(_push(channel, args.local, remote))
        print(f"Xong: {written} byte -> {remote}")
        return 0
    except ControlError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
