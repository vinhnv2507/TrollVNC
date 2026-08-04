"""Control IOS entry point.

    python main.py                       # mở giao diện
    python main.py --bonjour             # tìm qua mDNS (nhanh nhất, khuyên dùng)
    python main.py --scan 172.30.3.0/24  # quét rồi ghi vào config/devices.json
    python main.py --scan-arp            # quét theo bảng ARP
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from controlios.config import DEFAULT_PORT, DEFAULT_REGISTRY, Registry
from controlios.scan import arp_hosts, discover_bonjour, probe_hosts, scan_sync


def main() -> int:
    # Chế độ relay USB: khi đóng gói EXE, app tự re-launch chính nó với cờ này để
    # tunnel một cổng qua USB (tidevice được gói kèm exe). Chặn, chạy tới khi tắt.
    if len(sys.argv) == 5 and sys.argv[1] == "--usb-relay":
        from controlios.usb import run_relay
        try:
            run_relay(sys.argv[2], sys.argv[3], sys.argv[4])
        except Exception as exc:
            print(f"usb-relay lỗi: {exc}", file=sys.stderr)
            return 1
        return 0

    parser = argparse.ArgumentParser(prog="Control IOS")
    parser.add_argument("--scan", nargs="+", metavar="TARGET",
                        help="dải cần quét: 172.30.3.0/24, 172.30.3.10-90, hoặc IP")
    parser.add_argument("--bonjour", action="store_true",
                        help="tìm máy qua mDNS _rfb._tcp (TrollVNC tự quảng bá)")
    parser.add_argument("--bonjour-timeout", type=float, default=4.0,
                        help="số giây lắng nghe quảng bá mDNS (mặc định 4)")
    parser.add_argument("--scan-arp", action="store_true",
                        help="lấy IP từ bảng ARP rồi dò cổng VNC")
    parser.add_argument("--arp-prefix", default="172.30.",
                        help="tiền tố IP cần lọc trong bảng ARP (mặc định 172.30.)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.scan or args.scan_arp or args.bonjour:
        registry = Registry.load(DEFAULT_REGISTRY)
        if args.bonjour:
            print(f"Lắng nghe quảng bá _rfb._tcp trong {args.bonjour_timeout}s…")
            hosts = discover_bonjour(args.bonjour_timeout)
            if not hosts:
                print("Không thấy máy nào. Kiểm tra: TrollVNC đang chạy, PC và "
                      "iPhone cùng mạng LAN, mDNS không bị firewall chặn.")
        elif args.scan_arp:
            candidates = arp_hosts(args.arp_prefix)
            print(f"ARP: {len(candidates)} địa chỉ ứng viên")
            hosts = asyncio.run(probe_hosts(candidates, args.port))
        else:
            hosts = scan_sync(args.scan, args.port)
        added = registry.merge_hosts(hosts, args.port)
        registry.save(DEFAULT_REGISTRY)
        print(f"Tìm thấy {len(hosts)} máy TrollVNC, thêm mới {added}.")
        print(f"Đã ghi {DEFAULT_REGISTRY}")
        return 0

    from controlios.ui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
