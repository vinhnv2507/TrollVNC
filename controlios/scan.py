"""Find TrollVNC phones on the LAN.

Ba cách, từ tốt nhất xuống:

* ``discover_bonjour`` — TrollVNC tự quảng bá dịch vụ ``_rfb._tcp`` qua mDNS,
  nên hỏi thẳng mạng thay vì dò từng địa chỉ. Nhanh nhất và không bỏ sót máy
  đang bật, kể cả khi nó chưa từng liên lạc với PC này.
* ``arp_hosts``  — đọc bảng ARP của Windows rồi chỉ dò những IP đã từng liên
  lạc. Nhanh, nhưng bỏ sót máy chưa nói chuyện với PC.
* ``scan_cidr``  — dò cổng VNC trên từng địa chỉ của một subnet. Chậm nhất
  nhưng chắc chắn nhất; một /16 là 65k địa chỉ nên hãy quét /24.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import subprocess
import time
from typing import Iterable, List, Sequence

from .config import DEFAULT_PORT

_ARP_LINE = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{11,})\s+(\w+)")
CONTROL_DISCOVERY_PORT = 46752


def arp_hosts(prefix: str = "172.30.") -> List[str]:
    """IPv4 addresses from the local ARP cache, filtered by prefix."""

    try:
        out = subprocess.run(
            ["arp", "-a"], capture_output=True, text=True, timeout=15
        ).stdout
    except Exception:
        return []
    hosts = []
    for line in out.splitlines():
        m = _ARP_LINE.match(line)
        if m and m.group(1).startswith(prefix):
            hosts.append(m.group(1))
    return sorted(set(hosts), key=lambda h: tuple(int(p) for p in h.split(".")))


BONJOUR_SERVICE = "_rfb._tcp.local."


def discover_bonjour(timeout: float = 4.0, prefix: str = "") -> List[str]:
    """Máy TrollVNC tự quảng bá qua mDNS. Trả về danh sách ``ip:port``.

    Không cần biết subnet, không dò 254 địa chỉ. Cần cài ``zeroconf``; nếu
    thiếu thì trả về danh sách rỗng để phần quét thường vẫn chạy được.
    """

    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return []

    found: dict[str, int] = {}

    class Listener(ServiceListener):
        def _record(self, zc, service_type: str, name: str) -> None:
            info = zc.get_service_info(service_type, name, timeout=2000)
            if not info:
                return
            for address in info.parsed_addresses():
                if ":" in address:          # bỏ qua IPv6
                    continue
                if prefix and not address.startswith(prefix):
                    continue
                found[address] = info.port or DEFAULT_PORT

        def add_service(self, zc, service_type, name) -> None:
            self._record(zc, service_type, name)

        def update_service(self, zc, service_type, name) -> None:
            self._record(zc, service_type, name)

        def remove_service(self, zc, service_type, name) -> None:
            pass

    zeroconf = Zeroconf()
    try:
        browser = ServiceBrowser(zeroconf, BONJOUR_SERVICE, Listener())
        time.sleep(timeout)
        browser.cancel()
    finally:
        zeroconf.close()

    return [
        f"{host}:{port}"
        for host, port in sorted(
            found.items(), key=lambda kv: tuple(int(p) for p in kv[0].split("."))
        )
    ]


async def _tcp_port_open(host: str, port: int, timeout: float) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


async def _probe(host: str, port: int, timeout: float) -> bool:
    """Recognise ControlIOS through RFB or its dedicated control socket.

    A busy/already-connected RFB server can close a new probe without sending
    its banner. Port 46752 remains available independently of the VNC slot.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception:
        writer = None
    if writer is not None:
        try:
            banner = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
            if banner == b"RFB ":
                return True
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    return await _tcp_port_open(host, CONTROL_DISCOVERY_PORT, timeout)


async def probe_hosts(
    hosts: Iterable[str],
    port: int = DEFAULT_PORT,
    concurrency: int = 512,
    timeout: float = 1.5,
    progress=None,
) -> List[str]:
    sem = asyncio.Semaphore(concurrency)
    host_list = list(hosts)
    found: List[str] = []
    done = 0

    async def one(host: str) -> None:
        nonlocal done
        async with sem:
            ok = await _probe(host, port, timeout)
        done += 1
        if ok:
            found.append(host)
        if progress:
            progress(done, len(host_list), host if ok else None)

    await asyncio.gather(*(one(h) for h in host_list))
    return sorted(found, key=lambda h: tuple(int(p) for p in h.split(".")))


async def scan_cidr(
    cidr: str, port: int = DEFAULT_PORT, concurrency: int = 512,
    timeout: float = 1.5, progress=None,
) -> List[str]:
    net = ipaddress.ip_network(cidr, strict=False)
    return await probe_hosts(
        (str(ip) for ip in net.hosts()), port, concurrency, timeout, progress
    )


def scan_sync(targets: Sequence[str], port: int = DEFAULT_PORT, **kw) -> List[str]:
    """Blocking helper: each target is a CIDR, a range, or a bare IP."""

    hosts: List[str] = []
    for target in targets:
        if "/" in target:
            hosts.extend(str(ip) for ip in ipaddress.ip_network(target, strict=False).hosts())
        elif "-" in target:
            # "172.30.4.10-90"
            base, _, last = target.rpartition("-")
            prefix, _, first = base.rpartition(".")
            hosts.extend(f"{prefix}.{i}" for i in range(int(first), int(last) + 1))
        else:
            hosts.append(target)
    return asyncio.run(probe_hosts(hosts, port, **kw))
