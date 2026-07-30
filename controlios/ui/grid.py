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

    # Bề rộng ô tối thiểu khi tự chia cột, và khoảng cách giữa các ô.
    MIN_TILE_WIDTH = 120
    SPACING = 8
    MARGIN = 8

    def __init__(self, tile_width: int = 150, parent=None) -> None:
        super().__init__(parent)
        self.tile_width = tile_width
        self.tiles: Dict[str, DeviceTile] = {}
        self.order: List[str] = []
        self.selection: List[str] = []
        self._focus_key: Optional[str] = None
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
            # để trống một dải bên phải, và không bao giờ rộng hơn khung.
            tile_width = (available - self.SPACING * (columns - 1)) // columns
            tile_width = max(60, tile_width)

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
        self.tiers_changed.emit(tiers)
