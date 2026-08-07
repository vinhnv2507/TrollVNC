"""Device registry and runtime settings for Control IOS."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, List, Optional

DEFAULT_PORT = 5901

# Dải quét gợi ý sẵn trong hộp thoại Quét mạng.
DEFAULT_SCAN_RANGE = "172.30.3.0/24"

# Nơi để dữ liệu (config, captures...). Khi đóng gói EXE, để CẠNH file exe cho
# dễ mang đi máy khác; khi chạy từ mã nguồn thì ở gốc project.
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = PROJECT_ROOT / "config" / "devices.json"

# Thư viện kịch bản đặt tên, lưu ngay trong app (không cần file .txt rời).
DEFAULT_SCRIPTS = PROJECT_ROOT / "config" / "scripts.json"
# Thư viện kịch bản auto-click JavaScript (chạy TRÊN MÁY qua daemon).
DEFAULT_JS_SCRIPTS = PROJECT_ROOT / "config" / "autoclick_js.json"


def load_named_scripts(path: Path | str | None = None) -> dict:
    """Đọc thư viện kịch bản {tên: nội dung}. Thiếu/hỏng file thì trả về rỗng."""

    path = Path(path) if path is not None else DEFAULT_SCRIPTS
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(name): str(body) for name, body in data.items()}


def save_named_scripts(scripts: dict, path: Path | str | None = None) -> None:
    """Ghi thư viện kịch bản, nguyên tử (không hỏng file khi bị ngắt giữa chừng)."""

    path = Path(path) if path is not None else DEFAULT_SCRIPTS
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(scripts, indent=2, ensure_ascii=False)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


@dataclass
class DeviceSpec:
    """One TrollVNC iPhone."""

    host: str
    port: int = DEFAULT_PORT
    name: str = ""
    group: str = ""
    password: Optional[str] = None
    enabled: bool = True
    # Cổng control socket / SSH riêng cho máy này. None = dùng cổng chung trong
    # Settings. Dùng cho chế độ USB: mỗi máy map sang một cổng 127.0.0.1 khác
    # nhau (VNC = `port`, control = `control_port`, SSH = `ssh_port`).
    control_port: Optional[int] = None
    ssh_port: Optional[int] = None
    # UDID máy USB (để tự dựng lại relay khi mở app). Rỗng với máy mạng thường.
    udid: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.host

    @property
    def is_usb(self) -> bool:
        return bool(self.udid) or self.host in ("127.0.0.1", "localhost")

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

    # Cạnh dài của khung hình ở tier LIVE. 0 = giữ nguyên độ phân giải gốc.
    #
    # Mặc định là 0 vì việc thu nhỏ ở đây dùng bước nguyên: từ màn 1338 px chỉ
    # nhảy được xuống 669, mà khung điều khiển lại cao chừng 890 — tức là thu
    # nhỏ rồi *phóng ngược lên*, ảnh mờ đi mà chẳng nhanh hơn bao nhiêu. Đặt
    # khác 0 chỉ đáng khi mạng yếu và bạn chấp nhận mờ để đổi lấy nhẹ.
    live_long_edge: int = 0

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

    # SSH tới máy đã jailbreak. Mật khẩu mặc định của OpenSSH trên máy
    # jailbreak là "alpine" — đổi ngay sau khi cài, vì ai trong mạng LAN cũng
    # biết mật khẩu đó.
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str = ""
    # Khoá riêng để đăng nhập SSH bằng khoá thay vì mật khẩu. Đây là cách thoát
    # bế tắc "tài khoản bị khoá, chưa có mật khẩu" trên Dopamine: cài khoá công
    # khai vào máy qua control socket (chạy bằng root), rồi login bằng khoá.
    ssh_key_path: str = ""

    # Máy nằm ngoài khung nhìn quá bấy nhiêu giây thì ngắt hẳn kết nối, để
    # TrollVNC trên máy dừng chụp hình và trả CPU lại cho app đang chạy.
    # 0 = không bao giờ ngắt (giữ nguyên hành vi cũ).
    idle_disconnect_after: float = 60.0

    # Hệ số scale khung hình TrollVNC gửi về (0<scale<=1). 1.0 = gốc. Nhỏ hơn thì
    # máy nén khung nhẹ hơn -> mượt hơn trên máy đời cũ, đổi lại kém nét. Áp qua
    # control socket (setscale); cần TrollVNC đã vá vòng 5.
    device_scale: float = 1.0

    # Cuộn "thuận iOS": lăn bánh xe lên -> nội dung đi như vuốt trên iPhone. Bật
    # (mặc định) đảo chiều lăn cho khớp cảm giác cuộn của iOS; tắt để giữ chiều
    # kiểu desktop.
    natural_scroll: bool = True

    def validate(self) -> None:
        """Reject settings that would fail later inside the network thread."""

        positive = {
            "grid_fps": self.grid_fps,
            "live_fps": self.live_fps,
            "thumb_long_edge": self.thumb_long_edge,
            "connect_concurrency": self.connect_concurrency,
            "reconnect_delay": self.reconnect_delay,
            "reconnect_max": self.reconnect_max,
            "stall_timeout": self.stall_timeout,
            "control_port": self.control_port,
            "ssh_port": self.ssh_port,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"Cấu hình phải lớn hơn 0: {', '.join(invalid)}")
        if self.live_long_edge < 0 or self.max_connected < 0 \
                or self.idle_disconnect_after < 0:
            raise ValueError(
                "live_long_edge, max_connected và idle_disconnect_after không được âm"
            )
        if self.reconnect_max < self.reconnect_delay:
            raise ValueError("reconnect_max không được nhỏ hơn reconnect_delay")
        if not self.ssh_user.strip():
            raise ValueError("ssh_user không được để trống")
        if not (0.0 < self.device_scale <= 1.0):
            raise ValueError("device_scale phải trong khoảng (0, 1]")


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
        settings.validate()
        return cls(devices=devices, settings=settings)

    def save(self, path: Path | str = DEFAULT_REGISTRY) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.validate()
        payload = {
            "settings": asdict(self.settings),
            "devices": [asdict(d) for d in self.devices],
        }
        # Ghi file tạm cùng thư mục rồi replace nguyên tử: mất điện hoặc app bị
        # kill giữa lúc lưu sẽ không làm hỏng registry đang dùng.
        data = json.dumps(payload, indent=2, ensure_ascii=False)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

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
