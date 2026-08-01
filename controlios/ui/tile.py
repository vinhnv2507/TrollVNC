"""One phone in the grid."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..config import DeviceSpec
from ..vnc.session import Frame, State
from .detail import MOUSE_BUTTONS, WHEEL_STEP
from .image import qimage_for

STATE_COLOUR = {
    State.ONLINE: QColor("#3ddc84"),
    State.CONNECTING: QColor("#f0b429"),
    State.ERROR: QColor("#e5484d"),
    State.OFFLINE: QColor("#6b7280"),
    # Ngủ là do ta chủ động ngắt, không phải hỏng — màu riêng để khỏi tưởng lỗi.
    State.DORMANT: QColor("#4f8cff"),
}


LABEL_HEIGHT = 22
# Tỉ lệ ngang/dọc trước khi có khung hình đầu tiên (iPhone màn hình dài).
DEFAULT_ASPECT = 9 / 19.5


class DeviceTile(QWidget):
    clicked = Signal(str, object)      # key, modifiers
    activated = Signal(str)            # double click -> open detail

    # Chế độ điều khiển thẳng trên ô: toạ độ đã quy về framebuffer của máy đó.
    pressed_at = Signal(str, int, int, int)     # key, x, y, nút
    moved_at = Signal(str, int, int)
    released_at = Signal(str, int, int, int)
    scrolled_at = Signal(str, int, int, int, int)   # key, x, y, dx, dy

    def __init__(self, spec: DeviceSpec, tile_width: int = 150, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.state = State.OFFLINE
        self.detail = ""
        self.selected = False
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scaled_for = None
        self._tile_width = tile_width
        self._aspect = DEFAULT_ASPECT
        self._fb = (0, 0)              # kích thước framebuffer thật của máy
        self._image_rect = QRect()     # vùng ảnh được vẽ trong ô
        self.control_enabled = False
        self._dragging = False
        self._apply_size()
        self.setToolTip(spec.key)

    # -------------------------------------------------------------------- size

    def _apply_size(self) -> None:
        width = max(60, int(self._tile_width))
        height = int(width / self._aspect) + LABEL_HEIGHT
        self._scaled = None                  # đổi cỡ -> ảnh nhớ sẵn hết dùng được
        self.setFixedSize(width, height)

    def set_tile_width(self, width: int) -> None:
        if int(width) != self._tile_width:
            self._tile_width = int(width)
            self._apply_size()

    def set_aspect(self, aspect: float) -> None:
        """Tỉ lệ thật của máy, biết được sau khung hình đầu tiên."""

        if aspect > 0 and abs(aspect - self._aspect) > 0.001:
            self._aspect = aspect
            self._apply_size()

    # ------------------------------------------------------------------ inputs

    def set_frame(self, frame: Frame) -> None:
        if frame.full_width and frame.full_height:
            self.set_aspect(frame.full_width / frame.full_height)
            self._fb = (frame.full_width, frame.full_height)
        self._pixmap = QPixmap.fromImage(qimage_for(frame))
        self._scaled = None
        self.update()

    def set_state(self, state: State, detail: str = "") -> None:
        self.state = state
        self.detail = detail
        self.update()

    def set_selected(self, selected: bool) -> None:
        if selected != self.selected:
            self.selected = selected
            self.update()

    # ------------------------------------------------------------------ events

    def set_control_enabled(self, enabled: bool) -> None:
        self.control_enabled = enabled
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def _map(self, pos) -> tuple[int, int] | None:
        """Toạ độ trong ô -> toạ độ framebuffer của máy."""

        if not self._image_rect.isValid() or not self._fb[0]:
            return None
        if not self._image_rect.contains(pos):
            return None
        rx = (pos.x() - self._image_rect.x()) / self._image_rect.width()
        ry = (pos.y() - self._image_rect.y()) / self._image_rect.height()
        return int(rx * self._fb[0]), int(ry * self._fb[1])

    def mousePressEvent(self, event) -> None:
        # Ctrl/Shift vẫn để chọn máy, kể cả khi đang bật điều khiển — nếu không
        # thì bật chế độ này lên là không chọn được gì nữa.
        modifiers = event.modifiers()
        if self.control_enabled and not (modifiers & (Qt.ControlModifier | Qt.ShiftModifier)):
            button = MOUSE_BUTTONS.get(event.button())
            point = self._map(event.position().toPoint())
            if button is not None and point:
                self._dragging = True
                self.pressed_at.emit(self.spec.key, point[0], point[1], button)
            return
        self.clicked.emit(self.spec.key, modifiers)

    def mouseMoveEvent(self, event) -> None:
        if not (self.control_enabled and self._dragging):
            return
        point = self._map(event.position().toPoint())
        if point:
            self.moved_at.emit(self.spec.key, point[0], point[1])

    def mouseReleaseEvent(self, event) -> None:
        if not (self.control_enabled and self._dragging):
            return
        self._dragging = False
        button = MOUSE_BUTTONS.get(event.button())
        point = self._map(event.position().toPoint())
        if button is not None and point:
            self.released_at.emit(self.spec.key, point[0], point[1], button)

    def wheelEvent(self, event) -> None:
        if not self.control_enabled:
            return super().wheelEvent(event)
        point = self._map(event.position().toPoint())
        if not point:
            return
        delta = event.angleDelta()
        dy = delta.y() // WHEEL_STEP or (1 if delta.y() > 0 else -1 if delta.y() else 0)
        dx = delta.x() // WHEEL_STEP or (1 if delta.x() > 0 else -1 if delta.x() else 0)
        if dx or dy:
            self.scrolled_at.emit(self.spec.key, point[0], point[1], dx, dy)

    def mouseDoubleClickEvent(self, event) -> None:
        self.activated.emit(self.spec.key)

    # ----------------------------------------------------------------- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        body = QRect(0, 0, self.width(), self.height() - LABEL_HEIGHT)

        painter.fillRect(body, QColor("#111318"))
        if self._pixmap:
            # Thu phóng một lần cho mỗi khung hình. Chọn/bỏ chọn hay di chuột
            # đều vẽ lại ô, mà 250 ô cùng thu phóng lại thì rất tốn.
            if self._scaled is None or self._scaled_for != body.size():
                self._scaled = self._pixmap.scaled(
                    body.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._scaled_for = body.size()
            scaled = self._scaled
            self._image_rect = QRect(
                body.x() + (body.width() - scaled.width()) // 2,
                body.y() + (body.height() - scaled.height()) // 2,
                scaled.width(), scaled.height(),
            )
            painter.drawPixmap(self._image_rect.topLeft(), scaled)

        colour = STATE_COLOUR.get(self.state, QColor("#6b7280"))
        painter.setPen(QPen(QColor("#4f8cff") if self.selected else colour,
                            3 if self.selected else 1))
        painter.drawRect(body.adjusted(1, 1, -2, -2))

        label = QRect(0, self.height() - LABEL_HEIGHT, self.width(), LABEL_HEIGHT)
        painter.fillRect(label, QColor("#1b1f27"))
        painter.setPen(colour)
        painter.drawEllipse(6, self.height() - 15, 8, 8)
        painter.setPen(QColor("#d5d9e0"))
        text = self.spec.name if self.spec.name != self.spec.host else self.spec.host
        painter.drawText(
            label.adjusted(20, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
            painter.fontMetrics().elidedText(text, Qt.ElideMiddle, label.width() - 26),
        )
        painter.end()
