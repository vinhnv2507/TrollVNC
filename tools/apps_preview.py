"""Chụp ảnh giao diện có bảng Ứng dụng, dùng dữ liệu giả."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication              # noqa: E402

from controlios.config import Registry                  # noqa: E402
from controlios.control_channel import AppInfo          # noqa: E402
from controlios.ui.app import MainWindow                # noqa: E402
from controlios.vnc.session import State                # noqa: E402
from tools.ui_preview import fake_frame                 # noqa: E402

APPS = [
    AppInfo("com.facebook.Facebook", "Facebooku", "User", "480.0"),
    AppInfo("com.golike.app", "GoLike", "User", "2.1.0"),
    AppInfo("io.grass.app", "Grass", "User", "1.4"),
    AppInfo("com.honeygain.app", "Honeygain", "User", "3.0"),
    AppInfo("com.zing.zalo", "Zalo", "User", "24.10.1"),
    AppInfo("com.apple.Preferences", "Cài đặt", "System", "1.0"),
]


def main() -> int:
    output = sys.argv[1] if len(sys.argv) > 1 else "apps-preview.png"

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")

    registry_path = Path("config") / "_apps_preview.json"
    registry = Registry()
    registry.merge_hosts([f"172.30.0.{i}" for i in range(221, 245)])
    registry.settings.control_token = "Congavinh1"
    registry.save(registry_path)

    window = MainWindow(registry_path)
    window.resize(1600, 950)
    window.show()
    app.processEvents()

    for i, key in enumerate(window.grid.order):
        window.grid.on_status(key, State.ONLINE, "")
        window.grid.on_frame(fake_frame(key, i))

    key = window.grid.order[0]
    window._focus_device(key)
    window.detail.on_frame(fake_frame(key, 0, 376, 669))
    window.grid.select_all()

    window.apps_dock.show()
    window._apply_apps(key, APPS, "")
    window._fit_detail_pane()
    app.processEvents()

    ok = window.grab().save(output)
    print(f"{'da luu' if ok else 'LOI'} {output} · dock={window.apps_dock.width()}px "
          f"· {window.apps_panel.list.count()} app hien thi")

    window.close()
    registry_path.unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
