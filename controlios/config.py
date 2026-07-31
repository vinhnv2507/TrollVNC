"""Device registry and runtime settings for Control IOS."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, List, Optional

DEFAULT_PORT = 5901

# Dải quét gợi ý sẵn trong hộp thoại Quét mạng.
DEFAULT_SCAN_RANGE = "172.30.3.0/24"

# Where the registry lives by default (next to the project root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = PROJECT_ROOT / "config" / "devices.json"


@dataclass
class DeviceSpec:
    """One TrollVNC iPhone."""

    host: str
    port: int = DEFAULT_PORT
    name: str = ""
    group: str = ""
    password: Optional[str] = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.host

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class Settings:
    """Tuning knobs. Defaults are sized for ~250 devices on a LAN."""

    # Frames per second requested per tier.
    grid_fps: float = 1.0
    live_fps: float = 12.0

    # Long edge of a grid thumbnail, in pixels. Framebuffers are downscaled
    # inside the network thread so the UI never holds 250 full-size images.
    thumb_long_edge: int = 320

    # How many sessions may be doing their TCP+RFB handshake at once. Opening
    # 250 sockets simultaneously reliably trips iOS-side accept backlogs.
    connect_concurrency: int = 24

    # Hard cap on connected sessions. 0 = no cap (connect all of them).
    max_connected: int = 0

    # Seconds between reconnect attempts, doubled up to reconnect_max.
    reconnect_delay: float = 3.0
    reconnect_max: float = 60.0

    # Seconds without a frame before a session is considered stalled.
    stall_timeout: float = 20.0

    # Kênh điều khiển của TrollVNC đã vá (liệt kê/mở/đóng app). Token rỗng =
    # tắt hẳn, và mọi lệnh app sẽ báo lỗi rõ ràng thay vì treo.
    control_port: int = 46752
    control_token: str = ""


@dataclass
class Registry:
    devices: List[DeviceSpec] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_REGISTRY) -> "Registry":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        devices = [DeviceSpec(**d) for d in raw.get("devices", [])]
        settings = Settings(**raw.get("settings", {}))
        return cls(devices=devices, settings=settings)

    def save(self, path: Path | str = DEFAULT_REGISTRY) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "settings": asdict(self.settings),
            "devices": [asdict(d) for d in self.devices],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def merge_hosts(self, hosts: Iterable[str], port: int = DEFAULT_PORT) -> int:
        """Add hosts that are not in the registry yet. Returns the count added."""

        known = {d.key for d in self.devices}
        added = 0
        for host in hosts:
            if ":" in host:
                host, _, raw_port = host.partition(":")
                port_value = int(raw_port)
            else:
                port_value = port
            key = f"{host}:{port_value}"
            if key in known:
                continue
            self.devices.append(DeviceSpec(host=host, port=port_value))
            known.add(key)
            added += 1
        return added
