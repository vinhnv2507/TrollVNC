"""Chế độ USB: điều khiển iPhone qua dây cáp thay vì WiFi.

Cả ba kênh của Control IOS đều là TCP (VNC 5901, control socket 46752, SSH 22).
usbmuxd của Apple (đi kèm iTunes/Apple Mobile Device Support) cho **forward cổng
TCP của máy qua USB**. Ở đây dùng ``tidevice relay`` (thuần Python, cài bằng pip)
để mở mỗi cổng thành một cổng ``127.0.0.1`` trên PC; Control IOS nối tới đó y như
một máy mạng thường.

Mỗi máy chiếm một dải cổng cục bộ (VNC/control/SSH). Không đụng tới TrollVNC —
đây thuần tuý là lớp khám phá + tunnel ở phía PC.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import List, Optional

log = logging.getLogger(__name__)

# Cổng thật trên máy (đầu kia của relay).
DEVICE_VNC_PORT = 5901
DEVICE_CONTROL_PORT = 46752
DEVICE_SSH_PORT = 22

# Cổng cục bộ bắt đầu; mỗi máy chiếm một khối 10 cổng (dùng 3).
DEFAULT_BASE_PORT = 6001
PORT_STRIDE = 10

# Windows: không bật cửa sổ console cho mỗi relay.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def tidevice_available() -> bool:
    """tidevice + usbmuxd của Apple có sẵn sàng không (thư viện, chạy cả trong EXE)."""

    try:
        from tidevice import Usbmux
        Usbmux().device_list()
        return True
    except Exception as exc:
        log.warning("usbmux chưa sẵn sàng (thiếu tidevice hoặc Apple Mobile Device "
                    "Support?): %s", exc)
        return False


def list_usb_devices() -> List[dict]:
    """Máy iOS đang cắm USB: [{'udid', 'name'}...]. Rỗng nếu không có.

    Dùng thẳng thư viện tidevice (không gọi tiến trình ngoài) nên chạy được cả
    khi đóng gói EXE, miễn tidevice đã được gói kèm và Apple usbmuxd đang chạy.
    """

    try:
        from tidevice import Usbmux, Device
    except Exception as exc:
        log.warning("thiếu tidevice: %s", exc)
        return []
    try:
        um = Usbmux()
        infos = um.device_list()
    except Exception as exc:
        log.warning("không hỏi được usbmux: %s", exc)
        return []

    devices = []
    seen = set()
    for info in infos:
        udid = getattr(info, "udid", None)
        conn = str(getattr(info, "conn_type", "")).upper()
        if not udid or udid in seen or "USB" not in conn:
            continue          # chỉ lấy máy cắm cáp (bỏ WiFi-sync)
        seen.add(udid)
        try:
            name = Device(udid, um).name or udid[:8]
        except Exception:
            name = udid[:8]
        devices.append({"udid": udid, "name": name})
    return devices


def run_relay(udid: str, lport, rport) -> None:
    """Chạy relay một cổng qua USB (chặn, chạy mãi tới khi bị tắt).

    ``main.py`` gọi hàm này khi EXE tự re-launch ở chế độ ``--usb-relay`` — nhờ
    vậy bản đóng gói không cần tidevice.exe ngoài.
    """

    from tidevice import Device
    from tidevice._relay import relay
    relay(Device(udid), int(lport), int(rport))


def ports_for(index: int, base: int = DEFAULT_BASE_PORT) -> tuple:
    """(vnc, control, ssh) cục bộ cho máy thứ index."""
    start = base + index * PORT_STRIDE
    return start, start + 1, start + 2


class UsbRelayManager:
    """Dựng và giữ các tiến trình ``tidevice relay`` cho các máy USB.

    Mỗi cổng là một tiến trình con, cô lập: một relay chết không làm sập app.
    Gọi :meth:`stop` khi thoát để tắt hết.
    """

    def __init__(self) -> None:
        self._procs: List[subprocess.Popen] = []

    def _spawn(self, udid: str, local_port: int, device_port: int) -> None:
        if getattr(sys, "frozen", False):
            # Đóng gói EXE: tự re-launch chính exe ở chế độ relay (tidevice đã gói kèm).
            cmd = [sys.executable, "--usb-relay", udid, str(local_port), str(device_port)]
        else:
            # Chạy từ mã nguồn: dùng CLI tidevice.
            cmd = [sys.executable, "-m", "tidevice", "-u", udid, "relay",
                   str(local_port), str(device_port)]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
            )
            self._procs.append(proc)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("không dựng được relay %s:%d -> %d: %s",
                        udid[:8], local_port, device_port, exc)

    def start_for(self, devices: List[dict], base: int = DEFAULT_BASE_PORT,
                  with_ssh: bool = True) -> List[dict]:
        """Dựng relay cho từng máy, trả về danh sách spec để nạp vào lưới:

        ``[{'host':'127.0.0.1','port','control_port','ssh_port','name','udid'}]``
        """

        specs = []
        for index, dev in enumerate(devices):
            vnc, control, ssh = ports_for(index, base)
            self._spawn(dev["udid"], vnc, DEVICE_VNC_PORT)
            self._spawn(dev["udid"], control, DEVICE_CONTROL_PORT)
            if with_ssh:
                self._spawn(dev["udid"], ssh, DEVICE_SSH_PORT)
            specs.append({
                "host": "127.0.0.1",
                "port": vnc,
                "control_port": control,
                "ssh_port": ssh if with_ssh else None,
                "name": dev.get("name") or dev["udid"][:8],
                "udid": dev["udid"],
                "group": "usb",
            })
        return specs

    def restore(self, specs) -> None:
        """Dựng lại relay cho các máy USB đã lưu (mở lại app). ``specs`` là các
        đối tượng có ``udid``/``port``/``control_port``/``ssh_port``."""

        for spec in specs:
            udid = getattr(spec, "udid", "") or (spec.get("udid") if isinstance(spec, dict) else "")
            if not udid:
                continue
            port = getattr(spec, "port", None) or (spec.get("port") if isinstance(spec, dict) else None)
            cport = getattr(spec, "control_port", None) or (spec.get("control_port") if isinstance(spec, dict) else None)
            sport = getattr(spec, "ssh_port", None) or (spec.get("ssh_port") if isinstance(spec, dict) else None)
            if port:
                self._spawn(udid, port, DEVICE_VNC_PORT)
            if cport:
                self._spawn(udid, cport, DEVICE_CONTROL_PORT)
            if sport:
                self._spawn(udid, sport, DEVICE_SSH_PORT)

    def stop(self) -> None:
        for proc in self._procs:
            try:
                proc.terminate()
            except Exception:
                pass
        self._procs.clear()

    @property
    def count(self) -> int:
        return len(self._procs)
