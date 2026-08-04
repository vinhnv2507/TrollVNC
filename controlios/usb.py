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

import json
import logging
import shutil
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


def _tidevice_base() -> Optional[List[str]]:
    """Cách gọi tidevice, bền cả khi chạy từ mã nguồn lẫn khi đóng gói EXE.

    - Có ``tidevice``/``tidevice.exe`` trên PATH (đã ``pip install tidevice``): dùng nó.
    - Chạy từ mã nguồn (không đóng gói): ``python -m tidevice``.
    - Đóng gói EXE mà không có tidevice trên PATH: trả None -> USB không dùng được.
    """

    exe = shutil.which("tidevice")
    if exe:
        return [exe]
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "tidevice"]
    return None


def _tidevice_cmd(*args: str) -> Optional[List[str]]:
    base = _tidevice_base()
    return [*base, *args] if base else None


def tidevice_available() -> bool:
    cmd = _tidevice_cmd("version")
    if not cmd:
        return False
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                             creationflags=_NO_WINDOW)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def list_usb_devices() -> List[dict]:
    """Máy iOS đang cắm USB: [{'udid', 'name', 'serial'}...]. Rỗng nếu không có."""

    cmd = _tidevice_cmd("list", "--json", "--usb")
    if not cmd:
        return []
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("tidevice list lỗi: %s", exc)
        return []
    if out.returncode != 0:
        return []
    try:
        data = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []
    devices = []
    for item in data:
        udid = item.get("udid") or item.get("serialNumber")
        if not udid:
            continue
        devices.append({
            "udid": udid,
            "name": item.get("name") or item.get("serial") or udid[:8],
            "serial": item.get("serial", ""),
        })
    return devices


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
        cmd = _tidevice_cmd("-u", udid, "relay", str(local_port), str(device_port))
        if not cmd:
            log.warning("không có tidevice để dựng relay")
            return
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
