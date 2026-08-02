"""Chạy lệnh shell trên nhiều máy đã jailbreak, xem kết quả từng máy.

Đây cũng là công cụ để **dò xem lệnh nào thật sự chạy được trên iOS**: máy
jailbreak không có đủ bộ lệnh như Linux, và mỗi bản jailbreak lại khác nhau.
Thay vì đoán, chạy thử rồi đọc kết quả.
"""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

# Vài lệnh hay dùng, để bấm là chạy. Cái nào không có trên máy sẽ báo rõ.
PRESETS = [
    ("Kiểm tra SSH sống", "true"),
    ("Phiên bản iOS", "uname -a"),
    ("Dung lượng trống", "df -h /var"),
    ("Tiến trình đang chạy", "ps -Ao pid,comm"),
    ("Pin (ioreg)", "ioreg -c AppleSmartBattery -r -d 1"),
    ("App đang ở tiền cảnh", "ps -Ao comm | tail -40"),
    ("Thời gian bật máy", "uptime"),
    ("Khởi động lại SpringBoard", "killall -9 SpringBoard"),
]


class SshConsoleDialog(QDialog):
    run_requested = Signal(str)

    def __init__(self, target_count: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chạy lệnh qua SSH")
        self.resize(900, 560)
        self._rows: Dict[str, int] = {}

        layout = QVBoxLayout(self)

        self.target_label = QLabel("")
        layout.addWidget(self.target_label)
        self.set_targets(target_count)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Lệnh sẵn:"))
        self.presets = QComboBox()
        self.presets.addItem("— chọn —", "")
        for label, command in PRESETS:
            self.presets.addItem(label, command)
        self.presets.currentIndexChanged.connect(self._use_preset)
        preset_row.addWidget(self.presets, 1)
        layout.addLayout(preset_row)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Lệnh shell, ví dụ: uname -a")
        self.editor.setMaximumHeight(80)
        self.editor.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.editor)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Máy", "Mã", "Kết quả"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch(1)
        self.run_button = QPushButton("Chạy")
        self.run_button.clicked.connect(self._run)
        row.addWidget(self.run_button)
        close = QPushButton("Đóng")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)

    # ------------------------------------------------------------------ dữ liệu

    def set_targets(self, count: int) -> None:
        self.target_label.setText(
            f"Lệnh sẽ chạy song song trên <b>{count} máy</b> đang chọn."
            if count else "Chưa chọn máy nào."
        )

    def _use_preset(self, index: int) -> None:
        command = self.presets.itemData(index)
        if command:
            self.editor.setPlainText(command)

    def command(self) -> str:
        return self.editor.toPlainText().strip()

    def _run(self) -> None:
        if not self.command():
            self.status.setText("Chưa nhập lệnh.")
            return
        self.table.setRowCount(0)
        self._rows.clear()
        self.status.setText("Đang chạy…")
        self.run_button.setEnabled(False)
        self.run_requested.emit(self.command())

    # ------------------------------------------------------------------ kết quả

    def add_result(self, key: str, code: int, output: str) -> None:
        row = self._rows.get(key)
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._rows[key] = row
            self.table.setItem(row, 0, QTableWidgetItem(key))

        code_item = QTableWidgetItem("—" if code is None else str(code))
        if code:
            code_item.setForeground(Qt.red)
        self.table.setItem(row, 1, code_item)
        # Kết quả nhiều dòng dồn về một dòng cho dễ so sánh giữa các máy.
        self.table.setItem(row, 2, QTableWidgetItem(" ⏎ ".join(output.splitlines())))
        self.table.setToolTip(output)

    def finish(self, ok: int, failures: List[tuple]) -> None:
        self.run_button.setEnabled(True)
        total = ok + len(failures)
        self.status.setText(
            f"Xong trên {ok}/{total} máy."
            if not failures else
            f"Xong {ok}/{total} máy — {len(failures)} máy lỗi (xem cột Mã)."
        )
