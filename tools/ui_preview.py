"""Chụp ảnh giao diện với máy giả, để soi bố cục mà không cần iPhone thật.

    python tools/ui_preview.py out.png --devices 24 --columns 0
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication              # noqa: E402

from controlios.config import Registry                  # noqa: E402
from controlios.ui.app import MainWindow                # noqa: E402
from controlios.vnc.session import Frame, State         # noqa: E402

PHONE_W, PHONE_H = 752, 1338


def fake_frame(key: str, index: int, width: int = 94, height: int = 167) -> Frame:
    """Khung hình giả: dải màu + một vạch sáng để thấy rõ biên ảnh."""

    data = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            data[offset] = (30 + index * 17) % 256
            data[offset + 1] = int(200 * y / height)
            data[offset + 2] = int(200 * x / width)
    for x in range(width):                       # vạch trắng ở đỉnh
        for y in range(4):
            offset = (y * width + x) * 3
            data[offset] = data[offset + 1] = data[offset + 2] = 240
    return Frame(key=key, width=width, height=height, data=bytes(data),
                 full_width=PHONE_W, full_height=PHONE_H)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default="ui-preview.png")
    parser.add_argument("--devices", type=int, default=24)
    parser.add_argument("--columns", type=int, default=0)
    parser.add_argument("--size", default="1500x950")
    parser.add_argument("--focus", action="store_true",
                        help="mở một máy ở khung điều khiển bên phải")
    args = parser.parse_args()

    width, _, height = args.size.partition("x")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")

    registry_path = Path("config") / "_preview_devices.json"
    registry = Registry()
    registry.merge_hosts([f"172.30.3.{i}" for i in range(101, 101 + args.devices)])
    registry.save(registry_path)

    window = MainWindow(registry_path)
    window.resize(int(width), int(height))
    window.show()
    app.processEvents()

    if args.columns:
        index = [i for i, (_, c) in enumerate(
            __import__("controlios.ui.app", fromlist=["COLUMN_CHOICES"]).COLUMN_CHOICES
        ) if c == args.columns]
        if index:
            window.columns_combo.setCurrentIndex(index[0])
    app.processEvents()

    for i, key in enumerate(window.grid.order):
        window.grid.on_status(key, State.ONLINE, "")
        window.grid.on_frame(fake_frame(key, i))

    if args.focus and window.grid.order:
        key = window.grid.order[0]
        window._focus_device(key)
        window.detail.on_frame(fake_frame(key, 0, PHONE_W // 2, PHONE_H // 2))
        window._fit_detail_pane()

    app.processEvents()
    window._fit_detail_pane()
    app.processEvents()

    ok = window.grab().save(args.output)
    print(f"{'da luu' if ok else 'LOI khi luu'} {args.output}")
    print(f"cot={window.grid._columns} "
          f"o={next(iter(window.grid.tiles.values())).size().toTuple()} "
          f"splitter={window.splitter.sizes()}")

    window.close()
    registry_path.unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
