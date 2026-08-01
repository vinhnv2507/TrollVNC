"""Đo chi phí vẽ lại khung điều khiển — trước và sau khi nhớ ảnh đã thu phóng.

    python tools/bench_paint.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt                           # noqa: E402
from PySide6.QtGui import QImage, QPixmap               # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

from controlios.ui.detail import DetailView             # noqa: E402
from controlios.vnc.session import Frame                # noqa: E402

PHONE_W, PHONE_H = 752, 1338
PANE_W, PANE_H = 506, 890


def frame(key: str) -> Frame:
    return Frame(key=key, width=PHONE_W, height=PHONE_H,
                 data=bytes(PHONE_W * PHONE_H * 3),
                 full_width=PHONE_W, full_height=PHONE_H)


def main() -> int:
    app = QApplication.instance() or QApplication([])

    pixmap = QPixmap.fromImage(
        QImage(bytes(PHONE_W * PHONE_H * 3), PHONE_W, PHONE_H,
               PHONE_W * 3, QImage.Format_RGB888)
    )

    # Cách cũ: thu phóng lại trong mỗi lần vẽ.
    start = time.perf_counter()
    for _ in range(60):
        pixmap.scaled(PANE_W, PANE_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    per_scale = (time.perf_counter() - start) / 60 * 1000
    print(f"Thu phong mot lan:            {per_scale:.2f} ms")

    view = DetailView()
    view.resize(PANE_W, PANE_H)
    view.set_device("bench")
    view.show()
    app.processEvents()

    # Một khung hình, rồi vẽ lại nhiều lần như khi rê chuột.
    view.on_frame(frame("bench"))
    app.processEvents()

    start = time.perf_counter()
    for _ in range(200):
        view._scaled_pixmap()
    cached = (time.perf_counter() - start) / 200 * 1000
    print(f"Ve lai khi da nho san:        {cached:.4f} ms")

    print()
    print(f"Re chuot 200 lan giua hai khung hinh:")
    print(f"  cach cu  : {per_scale * 200:.0f} ms")
    print(f"  cach moi : {per_scale + cached * 200:.0f} ms  (thu phong dung 1 lan)")

    view.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
