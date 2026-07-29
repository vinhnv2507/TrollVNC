"""Full-rate interactive view of one phone."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ..vnc.session import Frame

# Qt key -> X11 keysym name understood by asyncvnc.
SPECIAL_KEYS = {
    Qt.Key_Return: "Return",
    Qt.Key_Enter: "Return",
    Qt.Key_Backspace: "BackSpace",
    Qt.Key_Delete: "Delete",
    Qt.Key_Escape: "Escape",
    Qt.Key_Tab: "Tab",
    Qt.Key_Up: "Up",
    Qt.Key_Down: "Down",
    Qt.Key_Left: "Left",
    Qt.Key_Right: "Right",
    Qt.Key_Home: "Home",
}


class DetailView(QWidget):
    """Emits input in framebuffer coordinates; the window routes it to the pool."""

    tap = Signal(int, int)
    drag_start = Signal(int, int)
    drag_move = Signal(int, int)
    drag_end = Signal(int, int)
    text_typed = Signal(str)
    key_pressed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.key: Optional[str] = None
        self._pixmap: Optional[QPixmap] = None
        self._fb = (0, 0)
        self._target = QRect()
        self._dragging = False
        self.setMinimumWidth(280)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background: #0b0d11;")

    @property
    def fb_size(self) -> tuple[int, int]:
        """Framebuffer size of the focused phone (0, 0) before the first frame."""
        return self._fb

    def set_device(self, key: Optional[str]) -> None:
        self.key = key
        self._pixmap = None
        self.update()

    def on_frame(self, frame: Frame) -> None:
        if frame.key != self.key:
            return
        image = QImage(
            frame.data, frame.width, frame.height, frame.width * 3, QImage.Format_RGB888
        )
        self._pixmap = QPixmap.fromImage(image.copy())
        self._fb = (frame.full_width, frame.full_height)
        self.update()

    # ------------------------------------------------------------- coordinates

    def _map(self, pos: QPoint) -> Optional[tuple[int, int]]:
        if not self._target.isValid() or not self._fb[0]:
            return None
        if not self._target.contains(pos):
            return None
        rx = (pos.x() - self._target.x()) / self._target.width()
        ry = (pos.y() - self._target.y()) / self._target.height()
        return int(rx * self._fb[0]), int(ry * self._fb[1])

    # ------------------------------------------------------------------ events

    def mousePressEvent(self, event) -> None:
        point = self._map(event.position().toPoint())
        if point:
            self._dragging = True
            self.drag_start.emit(*point)

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        point = self._map(event.position().toPoint())
        if point:
            self.drag_move.emit(*point)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        point = self._map(event.position().toPoint())
        if point:
            self.drag_end.emit(*point)

    def keyPressEvent(self, event) -> None:
        name = SPECIAL_KEYS.get(event.key())
        if name:
            self.key_pressed.emit(name)
        elif event.text():
            self.text_typed.emit(event.text())

    # ---------------------------------------------------------------- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b0d11"))
        if not self._pixmap:
            painter.setPen(QColor("#6b7280"))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Chọn một máy (double-click) để xem trực tiếp")
            painter.end()
            return

        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._target = QRect(
            (self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2,
            scaled.width(), scaled.height(),
        )
        painter.drawPixmap(self._target.topLeft(), scaled)
        painter.end()
