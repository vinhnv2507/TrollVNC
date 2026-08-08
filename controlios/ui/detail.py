"""Full-rate interactive view of one phone.

Chuyển sự kiện chuột/bàn phím của Qt thành sự kiện RFB, ở **toạ độ
framebuffer** — cửa sổ chính chỉ việc chuyển tiếp xuống pool. Widget này cũng
tự vẽ con trỏ, vì TrollVNC không gửi hình con trỏ về: không có nó thì bạn
không biết mình vừa chạm vào đâu.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..vnc.session import Frame
from .image import qimage_for

# Qt key -> tên keysym X11 mà asyncvnc hiểu.
SPECIAL_KEYS = {
    Qt.Key_Return: "Return",
    Qt.Key_Enter: "Return",
    Qt.Key_Backspace: "BackSpace",
    Qt.Key_Delete: "Delete",
    Qt.Key_Escape: "Escape",
    Qt.Key_Tab: "Tab",
    Qt.Key_Backtab: "ISO_Left_Tab",
    Qt.Key_Up: "Up",
    Qt.Key_Down: "Down",
    Qt.Key_Left: "Left",
    Qt.Key_Right: "Right",
    Qt.Key_Home: "Home",
    Qt.Key_End: "End",
    Qt.Key_PageUp: "Page_Up",
    Qt.Key_PageDown: "Page_Down",
    Qt.Key_Insert: "Insert",
    Qt.Key_Space: "space",
    Qt.Key_F1: "F1", Qt.Key_F2: "F2", Qt.Key_F3: "F3", Qt.Key_F4: "F4",
    Qt.Key_F5: "F5", Qt.Key_F6: "F6", Qt.Key_F7: "F7", Qt.Key_F8: "F8",
    Qt.Key_F9: "F9", Qt.Key_F10: "F10", Qt.Key_F11: "F11", Qt.Key_F12: "F12",
}

# Nút chuột Qt -> số nút RFB (0 = trái, 1 = giữa, 2 = phải).
MOUSE_BUTTONS = {
    Qt.LeftButton: 0,
    Qt.MiddleButton: 1,
    Qt.RightButton: 2,
}

# Bổ trợ Qt -> tên keysym. Thứ tự cố định để tổ hợp luôn nhất quán.
MODIFIERS = [
    (Qt.ControlModifier, "Ctrl"),
    (Qt.AltModifier, "Alt"),
    (Qt.MetaModifier, "Super"),
    (Qt.ShiftModifier, "Shift"),
]

# Một "nấc" bánh xe của Qt là 120 đơn vị.
WHEEL_STEP = 120

# Tỉ lệ ngang/dọc dùng trước khi có khung hình đầu tiên.
DEFAULT_ASPECT = 9 / 19.5


class DetailView(QWidget):
    pointer_pressed = Signal(int, int, int)     # x, y, nút
    pointer_moved = Signal(int, int)            # x, y (kể cả khi không giữ nút)
    pointer_released = Signal(int, int, int)
    scrolled = Signal(int, int, int, int)       # x, y, dx, dy (số nấc)
    text_typed = Signal(str)
    keys_pressed = Signal(list)                 # ["Ctrl", "c"]
    paste_requested = Signal()                  # Ctrl+V: dán chữ từ PC vào iOS
    copy_requested = Signal()                   # Ctrl+C: lấy clipboard iOS ra PC

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.key: Optional[str] = None
        self._pixmap: Optional[QPixmap] = None
        self._scaled: Optional[QPixmap] = None
        self._scaled_for = None
        self._fb = (0, 0)
        self._target = QRect()
        self._dragging = False
        self._cursor: Optional[QPoint] = None
        self._cursor_down = False
        self.setMinimumWidth(280)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)      # cần cho việc đọc toạ độ liên tục
        self.setStyleSheet("background: #0b0d11;")

    @property
    def fb_size(self) -> tuple[int, int]:
        """Framebuffer size of the focused phone (0, 0) before the first frame."""
        return self._fb

    @property
    def aspect(self) -> float:
        width, height = self._fb
        return width / height if width and height else DEFAULT_ASPECT

    def preferred_width(self) -> int:
        """Bề rộng vừa đúng một chiếc máy ở chiều cao hiện tại.

        Không có nó thì khung này chiếm nửa cửa sổ để hiển thị hai dải đen hai
        bên, còn lưới bị bóp lại.
        """

        return int(self.height() * self.aspect) + 2

    def set_device(self, key: Optional[str]) -> None:
        self.key = key
        self._pixmap = None
        self._image = None
        self._scaled = None
        self._cursor = None
        self._cursor_down = False
        self._dragging = False
        self.update()

    def clear_frame(self) -> None:
        """Bỏ ảnh đang hiển thị (giữ nguyên máy đang mở) — dùng khi nối lại để
        không giữ khung cũ/lồng lúc framebuffer đổi cỡ."""
        self._pixmap = None
        self._image = None
        self._scaled = None
        self.update()

    def color_at_fb(self, x: int, y: int) -> Optional[str]:
        """Mã màu RRGGBB tại điểm framebuffer (x, y), hoặc None. Dùng cho 'get
        color' — lấy màu để viết matchColor/waitColor trong kịch bản."""
        image = self._image
        if image is None or image.isNull():
            return None
        fw, fh = self._fb
        if not fw or not fh:
            return None
        ix = max(0, min(image.width() - 1, int(x / fw * image.width())))
        iy = max(0, min(image.height() - 1, int(y / fh * image.height())))
        c = image.pixelColor(ix, iy)
        return f"{c.red():02X}{c.green():02X}{c.blue():02X}"

    def on_frame(self, frame: Frame) -> None:
        if frame.key != self.key:
            return
        # fromImage đã sao chép điểm ảnh vào pixmap, nên .copy() ở đây là thừa
        # một lần sao chép cả khung hình.
        image = qimage_for(frame)
        self._image = image                  # giữ để đọc màu điểm ảnh (get color)
        self._pixmap = QPixmap.fromImage(image)
        self._fb = (frame.full_width, frame.full_height)
        self._scaled = None                  # khung mới -> phải thu phóng lại
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
        button = MOUSE_BUTTONS.get(event.button())
        if button is None:
            return
        point = self._map(event.position().toPoint())
        if point:
            self._dragging = True
            self._cursor = event.position().toPoint()
            self._cursor_down = True
            self.pointer_pressed.emit(point[0], point[1], button)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        position = event.position().toPoint()
        point = self._map(position)
        if not point:
            return
        self._cursor = position
        self.pointer_moved.emit(*point)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        button = MOUSE_BUTTONS.get(event.button())
        if button is None or not self._dragging:
            return
        self._dragging = False
        self._cursor_down = False
        point = self._map(event.position().toPoint())
        if point:
            self.pointer_released.emit(point[0], point[1], button)
        self.update()

    def wheelEvent(self, event) -> None:
        point = self._map(event.position().toPoint())
        if not point:
            return
        delta = event.angleDelta()
        dy = delta.y() // WHEEL_STEP
        dx = delta.x() // WHEEL_STEP
        # Bánh xe nhích ít hơn một nấc vẫn phải cuộn, nếu không cảm giác là kẹt.
        if dy == 0 and delta.y():
            dy = 1 if delta.y() > 0 else -1
        if dx == 0 and delta.x():
            dx = 1 if delta.x() > 0 else -1
        if dx or dy:
            self.scrolled.emit(point[0], point[1], dx, dy)

    def keyPressEvent(self, event) -> None:
        # Ctrl+V / Ctrl+C: cầu nối clipboard PC <-> iOS (không gửi phím thô).
        mods = event.modifiers()
        if (mods & Qt.ControlModifier) and not (mods & Qt.AltModifier):
            if event.key() == Qt.Key_V:
                self.paste_requested.emit()
                return
            if event.key() == Qt.Key_C:
                self.copy_requested.emit()
                return

        # Không chặn autoRepeat: giữ phím thì máy nhận nhiều lần, đúng như thật.
        modifiers = self._modifier_names(event.modifiers())
        name = SPECIAL_KEYS.get(event.key())

        if name:
            self.keys_pressed.emit(modifiers + [name])
            return

        text = event.text()
        if modifiers and text:
            # Ctrl+C: gửi tổ hợp thay vì ký tự điều khiển thô.
            base = self._base_char(event)
            if base:
                self.keys_pressed.emit(modifiers + [base])
                return
        if text and text.isprintable():
            self.text_typed.emit(text)

    @staticmethod
    def _modifier_names(modifiers) -> List[str]:
        return [name for flag, name in MODIFIERS if modifiers & flag]

    @staticmethod
    def _base_char(event) -> Optional[str]:
        """Ký tự gốc của phím, bỏ qua phần bổ trợ (Ctrl+C -> 'c')."""

        code = event.key()
        if Qt.Key_A <= code <= Qt.Key_Z:
            return chr(code).lower()
        if Qt.Key_0 <= code <= Qt.Key_9:
            return chr(code)
        text = event.text()
        return text if text and text.isprintable() else None

    # ---------------------------------------------------------------- painting

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scaled = None

    def _scaled_pixmap(self) -> QPixmap:
        """Thu phóng **một lần cho mỗi khung hình**, rồi dùng lại.

        Trước đây việc này nằm thẳng trong paintEvent, mà rê chuột thì gọi
        update() liên tục — hoá ra mỗi lần chuột nhúc nhích là thu phóng lại cả
        khung 752×1338 bằng SmoothTransformation. Đó là nguyên nhân chính khiến
        thao tác thấy giật.
        """

        if self._scaled is None or self._scaled_for != self.size():
            self._scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._scaled_for = self.size()
        return self._scaled

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b0d11"))
        if not self._pixmap:
            painter.setPen(QColor("#6b7280"))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Chọn một máy (double-click) để xem trực tiếp")
            painter.end()
            return

        scaled = self._scaled_pixmap()
        self._target = QRect(
            (self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2,
            scaled.width(), scaled.height(),
        )
        painter.drawPixmap(self._target.topLeft(), scaled)
        self._draw_cursor(painter)
        painter.end()

    def _draw_cursor(self, painter: QPainter) -> None:
        """Vòng ngắm tại vị trí con trỏ — TrollVNC không gửi hình con trỏ về."""

        if self._cursor is None or not self._target.contains(self._cursor):
            return
        colour = QColor("#ff4d4f") if self._cursor_down else QColor("#4f8cff")
        radius = 9 if self._cursor_down else 7
        painter.setPen(QPen(colour, 2))
        painter.drawEllipse(self._cursor, radius, radius)
        painter.drawLine(self._cursor.x() - radius - 4, self._cursor.y(),
                         self._cursor.x() - 3, self._cursor.y())
        painter.drawLine(self._cursor.x() + 3, self._cursor.y(),
                         self._cursor.x() + radius + 4, self._cursor.y())
        painter.drawLine(self._cursor.x(), self._cursor.y() - radius - 4,
                         self._cursor.x(), self._cursor.y() - 3)
        painter.drawLine(self._cursor.x(), self._cursor.y() + 3,
                         self._cursor.x(), self._cursor.y() + radius + 4)
