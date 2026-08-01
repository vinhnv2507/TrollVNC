"""One phone in the grid."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..config import DeviceSpec
from ..vnc.session import Frame, State

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

    def __init__(self, spec: DeviceSpec, tile_width: int = 150, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.state = State.OFFLINE
        self.detail = ""
        self.selected = False
        self._pixmap: QPixmap | None = None
        self._tile_width = tile_width
        self._aspect = DEFAULT_ASPECT
        self._apply_size()
        self.setToolTip(spec.key)

    # -------------------------------------------------------------------- size

    def _apply_size(self) -> None:
        width = max(60, int(self._tile_width))
        height = int(width / self._aspect) + LABEL_HEIGHT
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
        image = QImage(
            frame.data, frame.width, frame.height, frame.width * 3, QImage.Format_RGB888
        )
        self._pixmap = QPixmap.fromImage(image.copy())
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

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.spec.key, event.modifiers())

    def mouseDoubleClickEvent(self, event) -> None:
        self.activated.emit(self.spec.key)

    # ----------------------------------------------------------------- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        body = QRect(0, 0, self.width(), self.height() - LABEL_HEIGHT)

        painter.fillRect(body, QColor("#111318"))
        if self._pixmap:
            scaled = self._pixmap.scaled(
                body.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                body.x() + (body.width() - scaled.width()) // 2,
                body.y() + (body.height() - scaled.height()) // 2,
                scaled,
            )

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
