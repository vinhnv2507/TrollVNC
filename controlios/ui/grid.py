"""Scrollable wall of tiles.

Only tiles inside (or just outside) the viewport are promoted to the GRID
tier — that is what keeps 250 connected phones from costing 250 video streams.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QScrollArea, QWidget

from ..config import DeviceSpec
from ..vnc.session import Frame, State, Tier
from .tile import DeviceTile

# Rows of tiles kept warm above and below the viewport, so scrolling does not
# show empty cells while the first frame arrives.
PREFETCH_ROWS = 1


class DeviceGrid(QScrollArea):
    tiers_changed = Signal(dict)
    selection_changed = Signal(list)
    device_activated = Signal(str)

    # Điều khiển thẳng trên ô (toạ độ đã quy về framebuffer của máy)
    tile_pressed = Signal(str, int, int, int)
    tile_moved = Signal(str, int, int)
    tile_released = Signal(str, int, int, int)
    tile_scrolled = Signal(str, int, int, int, int)

    # Bề rộng ô tối thiểu khi tự chia cột, và khoảng cách giữa các ô.
    MIN_TILE_WIDTH = 120
    # Bề rộng ô tối đa: khi ít máy (1-2), đừng để ô giãn hết bề rộng cửa sổ rồi
    # phóng to ảnh thu nhỏ lên thành mờ tịt. Nhiều máy thì mỗi ô vẫn nhỏ hơn mức
    # này nên vẫn chia đều khít.
    MAX_TILE_WIDTH = 300
    SPACING = 8
    MARGIN = 8

    def __init__(self, tile_width: int = 150, parent=None) -> None:
        super().__init__(parent)
        self.tile_width = tile_width
        self.tiles: Dict[str, DeviceTile] = {}
        self.order: List[str] = []
        self.selection: List[str] = []
        self._focus_key: Optional[str] = None
        self.control_enabled = False
        #: ô vừa được thao tác — tạm nâng nhịp để thấy phản hồi
        self._control_key: Optional[str] = None
        self._columns = 0            # số cột đang dùng
        self._forced_columns = 0     # 0 = tự động
        self._laying_out = False

        self._body = QWidget()
        self._layout = QGridLayout(self._body)
        self._layout.setSpacing(self.SPACING)
        self._layout.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        self._layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setWidget(self._body)
        self.setWidgetResizable(True)
        # Ô luôn được chia vừa bề rộng nên không bao giờ cần cuộn ngang; để bật
        # thì ô bị cắt mất một phần như trước.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(self.MIN_TILE_WIDTH * 2 + self.SPACING + self.MARGIN * 2 + 20)
        self.setStyleSheet("QScrollArea { background: #0b0d11; border: none; }")

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._publish_tiers)
        self.verticalScrollBar().valueChanged.connect(self._debounce.start)

        # Ô vừa thao tác giữ nhịp cao thêm vài giây rồi trả về bình thường.
        self._control_timer = QTimer(self)
        self._control_timer.setSingleShot(True)
        self._control_timer.setInterval(4000)
        self._control_timer.timeout.connect(self._release_control_boost)

    # ---------------------------------------------------------------- contents

    def set_devices(self, specs: List[DeviceSpec]) -> None:
        for tile in self.tiles.values():
            tile.setParent(None)
            tile.deleteLater()
        self.tiles.clear()
        self.order = []
        self.selection = []

        for spec in specs:
            tile = DeviceTile(spec, self.tile_width, self._body)
            tile.clicked.connect(self._on_tile_clicked)
            tile.activated.connect(self.device_activated)
            tile.set_control_enabled(self.control_enabled)
            tile.pressed_at.connect(self._on_tile_pressed)
            tile.moved_at.connect(self.tile_moved)
            tile.released_at.connect(self.tile_released)
            tile.scrolled_at.connect(self._on_tile_scrolled)
            self.tiles[spec.key] = tile
            self.order.append(spec.key)

        self._columns = 0
        self._relayout()
        self.selection_changed.emit(self.selection)

    def on_frame(self, frame: Frame) -> None:
        tile = self.tiles.get(frame.key)
        if tile:
            tile.set_frame(frame)

    def on_status(self, key: str, state: State, detail: str) -> None:
        tile = self.tiles.get(key)
        if tile:
            tile.set_state(state, detail)

    # -------------------------------------------------------------- selection

    def _on_tile_clicked(self, key: str, modifiers) -> None:
        if modifiers & Qt.ControlModifier:
            if key in self.selection:
                self.selection.remove(key)
            else:
                self.selection.append(key)
        elif modifiers & Qt.ShiftModifier and self.selection:
            start = self.order.index(self.selection[-1])
            end = self.order.index(key)
            lo, hi = sorted((start, end))
            for k in self.order[lo:hi + 1]:
                if k not in self.selection:
                    self.selection.append(k)
        else:
            self.selection = [key]

        for k, tile in self.tiles.items():
            tile.set_selected(k in self.selection)
        self.selection_changed.emit(list(self.selection))

    def select_all(self) -> None:
        self.selection = list(self.order)
        for tile in self.tiles.values():
            tile.set_selected(True)
        self.selection_changed.emit(list(self.selection))

    def clear_selection(self) -> None:
        self.selection = []
        for tile in self.tiles.values():
            tile.set_selected(False)
        self.selection_changed.emit([])

    # ------------------------------------------------------------------ layout

    def set_columns(self, columns: int) -> None:
        """0 = tự chia theo bề rộng."""

        if columns != self._forced_columns:
            self._forced_columns = columns
            self._columns = 0          # buộc xếp lại
            self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if self._laying_out or not self.order:
            return
        self._laying_out = True
        try:
            available = max(
                self.MIN_TILE_WIDTH,
                self.viewport().width() - self.MARGIN * 2,
            )
            columns = self._forced_columns or max(
                1, (available + self.SPACING) // (self.tile_width + self.SPACING)
            )
            columns = max(1, min(columns, len(self.order)))

            # Chia đều bề rộng còn lại cho các cột: ô giãn cho vừa khít thay vì
            # để trống một dải bên phải, và không bao giờ rộng hơn khung. Nhưng
            # chặn ở MAX_TILE_WIDTH để 1-2 máy không bị phóng to đầy màn hình.
            tile_width = (available - self.SPACING * (columns - 1)) // columns
            tile_width = max(60, min(tile_width, self.MAX_TILE_WIDTH))

            for tile in self.tiles.values():
                tile.set_tile_width(tile_width)

            if columns != self._columns:
                self._columns = columns
                while self._layout.count():
                    self._layout.takeAt(0)
                for index, key in enumerate(self.order):
                    self._layout.addWidget(
                        self.tiles[key], index // columns, index % columns
                    )
        finally:
            self._laying_out = False
        self._debounce.start()

    # ------------------------------------------------------------------- tiers

    def set_control_enabled(self, enabled: bool) -> None:
        """Bật thì bấm thẳng vào ô là điều khiển máy, không phải chọn máy."""

        self.control_enabled = enabled
        for tile in self.tiles.values():
            tile.set_control_enabled(enabled)
        if not enabled:
            self._control_key = None
            self._publish_tiers()

    def _on_tile_pressed(self, key: str, x: int, y: int, button: int) -> None:
        # Ô trong lưới chỉ làm mới 1 hình/giây, bấm vào mà chờ một giây mới thấy
        # phản hồi thì không dùng được. Nâng riêng ô đang thao tác lên nhịp cao.
        if key != self._control_key:
            self._control_key = key
            self._publish_tiers()
        self._control_timer.start()
        self.tile_pressed.emit(key, x, y, button)

    def _on_tile_scrolled(self, key: str, x: int, y: int, dx: int, dy: int) -> None:
        if key != self._control_key:
            self._control_key = key
            self._publish_tiers()
        self._control_timer.start()
        self.tile_scrolled.emit(key, x, y, dx, dy)

    def _release_control_boost(self) -> None:
        if self._control_key is not None:
            self._control_key = None
            self._publish_tiers()

    def set_focus_key(self, key: Optional[str]) -> None:
        """The device shown in the detail pane gets the LIVE tier."""
        self._focus_key = key
        self._publish_tiers()

    def _publish_tiers(self) -> None:
        if not self.order:
            return
        columns = max(1, self._columns)
        row_height = self.tiles[self.order[0]].height() + self._layout.spacing()
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()

        first_row = max(0, top // row_height - PREFETCH_ROWS)
        last_row = bottom // row_height + PREFETCH_ROWS
        first = first_row * columns
        last = (last_row + 1) * columns

        tiers = {key: Tier.IDLE for key in self.order}
        for key in self.order[first:last]:
            tiers[key] = Tier.GRID
        if self._focus_key in tiers:
            tiers[self._focus_key] = Tier.LIVE
        if self._control_key in tiers:
            tiers[self._control_key] = Tier.LIVE
        self.tiers_changed.emit(tiers)
