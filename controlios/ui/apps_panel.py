"""Bảng ứng dụng: danh sách app đã cài của máy đang mở.

Bấm một app để mở nó, chuột phải để đóng. Thao tác áp cho **các máy đang
chọn** ở lưới, nên mở một app trên 50 máy cũng chỉ là một cú bấm.
"""

from __future__ import annotations

import zlib
from typing import List, Optional

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from ..control_channel import AppInfo

ICON_SIZE = 34

# Bảng màu cho biểu tượng thay thế. Không lấy được icon thật từ máy nên dùng
# chữ cái đầu trên nền màu — vẫn phân biệt nhanh bằng mắt trong danh sách dài.
ICON_COLOURS = [
    "#4f8cff", "#3ddc84", "#f0b429", "#e5484d", "#a855f7",
    "#06b6d4", "#f97316", "#ec4899", "#84cc16", "#6366f1",
]


def colour_index(bundle_id: str) -> int:
    """Chọn màu theo bundle id, **ổn định giữa các lần chạy**.

    Không dùng hash() của Python: giá trị đó ngẫu nhiên hoá theo tiến trình,
    nên màu biểu tượng sẽ đổi mỗi lần mở phần mềm.
    """

    return zlib.crc32(bundle_id.encode("utf-8")) % len(ICON_COLOURS)


def letter_icon(app: AppInfo) -> QIcon:
    """Biểu tượng thay thế: chữ cái đầu trên nền màu ổn định theo bundle id."""

    colour = QColor(ICON_COLOURS[colour_index(app.bundle_id)])
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(colour))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, ICON_SIZE, ICON_SIZE, 9, 9)

    letter = (app.display_name.strip() or "?")[0].upper()
    font = QFont()
    font.setPointSize(15)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, letter)
    painter.end()

    return QIcon(pixmap)


# Thao tác ở mức **thiết bị**, không thuộc app nào. Đây là những thứ kênh điều
# khiển không làm được nên vẫn phải dùng nút cứng qua cử chỉ.
DEVICE_ACTIONS = [
    ("⌂ Home", "home", "Về màn hình chính (nút Home)"),
    ("⇄ Chuyển app", "switcher", "Mở trình chuyển app (bấm Home hai lần)"),
    ("⏻ Khoá", "lock", "Khoá máy (nút Power)"),
]


class AppsPanel(QWidget):
    launch_requested = Signal(str)       # bundle id
    terminate_requested = Signal(str)
    wipe_requested = Signal(str)         # xoá dữ liệu app (như cài lại)
    snapshot_requested = Signal(str)     # lưu snapshot dữ liệu app
    restore_requested = Signal(str)      # khôi phục dữ liệu app từ snapshot
    refresh_requested = Signal()
    gesture_requested = Signal(str)      # tên cử chỉ: home / switcher / lock

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._apps: List[AppInfo] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # Hàng thao tác thiết bị: gộp từ menu "Thao tác app" cũ về đây, để mọi
        # thứ liên quan tới app và máy nằm chung một chỗ.
        device_row = QHBoxLayout()
        device_row.setSpacing(4)
        self.device_buttons = {}
        for label, gesture, tip in DEVICE_ACTIONS:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _checked=False, g=gesture: self.gesture_requested.emit(g)
            )
            device_row.addWidget(button)
            self.device_buttons[gesture] = button
        layout.addLayout(device_row)

        row = QHBoxLayout()
        self.refresh_button = QPushButton("Nạp danh sách")
        self.refresh_button.clicked.connect(self.refresh_requested)
        row.addWidget(self.refresh_button)
        self.user_only = QCheckBox("Chỉ app tự cài")
        self.user_only.setChecked(True)
        self.user_only.toggled.connect(self._rebuild)
        row.addWidget(self.user_only)
        layout.addLayout(row)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Lọc theo tên hoặc bundle id…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._rebuild)
        layout.addWidget(self.filter)

        self.target_label = QLabel("")
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        self.list = QListWidget()
        self.list.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemClicked.connect(self._on_activated)
        layout.addWidget(self.list, 1)

        # Độ sáng / Cài .ipa / Đẩy file đã chuyển ra thanh công cụ chính.

        # Backup/khôi phục DỮ LIỆU của app ĐANG CHỌN trong danh sách. (Cũng có ở
        # chuột phải, nhưng để nút cho dễ thấy.)
        data_label = QLabel("Dữ liệu app đang chọn:")
        data_label.setStyleSheet("color: #9aa4b2;")
        layout.addWidget(data_label)
        data_row = QHBoxLayout()
        data_row.setSpacing(4)
        self.snapshot_button = QPushButton("💾 Snapshot…")
        self.snapshot_button.setToolTip(
            "Lưu một bản dữ liệu của app đang chọn (nhiều bản có tên) — cần "
            "TrollVNC đã vá")
        self.snapshot_button.clicked.connect(
            lambda: self._emit_for_selected(self.snapshot_requested))
        data_row.addWidget(self.snapshot_button)

        self.restore_button = QPushButton("↩ Khôi phục…")
        self.restore_button.setToolTip(
            "Mở danh sách snapshot của app đang chọn để khôi phục hoặc xoá bản")
        self.restore_button.clicked.connect(
            lambda: self._emit_for_selected(self.restore_requested))
        data_row.addWidget(self.restore_button)

        self.wipe_button = QPushButton("🧹 Xoá data")
        self.wipe_button.setToolTip("Xoá dữ liệu app đang chọn như vừa cài lại (giữ keychain)")
        self.wipe_button.clicked.connect(
            lambda: self._emit_for_selected(self.wipe_requested))
        data_row.addWidget(self.wipe_button)
        layout.addLayout(data_row)

        self.status = QLabel("Chọn một máy rồi bấm Nạp danh sách.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #9aa4b2;")
        layout.addWidget(self.status)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    # ------------------------------------------------------------------ trạng thái

    def _emit_for_selected(self, signal) -> None:
        """Phát tín hiệu backup/khôi phục/xoá cho app đang chọn trong danh sách."""

        bundle = self.selected_bundle()
        if bundle:
            signal.emit(bundle)
        else:
            self.status.setText("Hãy chọn một app trong danh sách trước.")

    def set_targets(self, count: int) -> None:
        self.target_label.setText(
            f"Thao tác sẽ áp cho <b>{count} máy</b> đang chọn."
            if count > 1 else
            "Thao tác áp cho máy đang mở." if count == 1 else
            "Chưa chọn máy nào."
        )

    def set_loading(self) -> None:
        self.status.setText("Đang hỏi máy…")
        self.refresh_button.setEnabled(False)

    def set_busy(self, message: str) -> None:
        self.note.setStyleSheet("color: #9aa4b2;")
        self.note.setText(message)

    def set_note(self, message: str, error: bool = False) -> None:
        """Kết quả của thao tác hàng loạt gần nhất — **ở lại** cho tới lần sau.

        Một dòng thoáng qua ở thanh trạng thái rất dễ bỏ sót, mà đây lại đúng
        chỗ người dùng cần biết máy nào không làm được và vì sao.
        """

        self.note.setStyleSheet("color: #e5484d;" if error else "color: #3ddc84;")
        self.note.setText(message)

    def set_error(self, message: str) -> None:
        self._apps = []
        self.list.clear()
        self.refresh_button.setEnabled(True)
        self.status.setText(message)
        self.status.setStyleSheet("color: #e5484d;")

    def set_apps(self, apps: List[AppInfo]) -> None:
        self._apps = list(apps)
        self.refresh_button.setEnabled(True)
        self.status.setStyleSheet("color: #9aa4b2;")
        self._rebuild()

    # ------------------------------------------------------------------ danh sách

    def _visible_apps(self) -> List[AppInfo]:
        needle = self.filter.text().strip().lower()
        apps = self._apps
        if self.user_only.isChecked():
            apps = [a for a in apps if a.is_user_app]
        if needle:
            apps = [
                a for a in apps
                if needle in a.display_name.lower() or needle in a.bundle_id.lower()
            ]
        return apps

    def _rebuild(self) -> None:
        self.list.clear()
        apps = self._visible_apps()
        for app in apps:
            item = QListWidgetItem(letter_icon(app), app.display_name)
            item.setData(Qt.UserRole, app.bundle_id)
            tooltip = f"{app.bundle_id}\nLoại: {app.kind}"
            if app.version:
                tooltip += f"\nPhiên bản: {app.version}"
            item.setToolTip(tooltip)
            self.list.addItem(item)

        if self._apps:
            self.status.setText(
                f"{len(apps)}/{len(self._apps)} app · bấm để mở, chuột phải để đóng"
            )

    def selected_bundle(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    # ------------------------------------------------------------------ thao tác

    def _on_activated(self, item: QListWidgetItem) -> None:
        self.launch_requested.emit(item.data(Qt.UserRole))

    def _context_menu(self, position) -> None:
        item = self.list.itemAt(position)
        if not item:
            return
        bundle = item.data(Qt.UserRole)

        menu = QMenu(self)
        open_action = QAction(f"Mở {item.text()}", menu)
        open_action.triggered.connect(lambda: self.launch_requested.emit(bundle))
        menu.addAction(open_action)

        close_action = QAction(f"Đóng {item.text()}", menu)
        close_action.triggered.connect(lambda: self.terminate_requested.emit(bundle))
        menu.addAction(close_action)

        menu.addSeparator()
        # Reset dữ liệu app (chỉ đụng /var — chạy cả trên máy chỉ có TrollStore).
        wipe_action = QAction("Xoá dữ liệu (như cài lại)", menu)
        wipe_action.triggered.connect(lambda: self.wipe_requested.emit(bundle))
        menu.addAction(wipe_action)

        snapshot_action = QAction("Lưu snapshot dữ liệu…", menu)
        snapshot_action.triggered.connect(lambda: self.snapshot_requested.emit(bundle))
        menu.addAction(snapshot_action)

        restore_action = QAction("Snapshot & khôi phục…", menu)
        restore_action.triggered.connect(lambda: self.restore_requested.emit(bundle))
        menu.addAction(restore_action)

        menu.addSeparator()
        copy_action = QAction("Chép bundle id", menu)
        copy_action.triggered.connect(lambda: self._copy(bundle))
        menu.addAction(copy_action)

        menu.exec(self.list.viewport().mapToGlobal(position))

    @staticmethod
    def _copy(text: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
