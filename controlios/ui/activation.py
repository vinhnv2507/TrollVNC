"""LAN license activation remains available when viewing is expired."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QHeaderView)


class ActivationDialog(QDialog):
    _result = Signal(str, object, str, bool)
    activated = Signal(str)

    def __init__(self, pool, devices, parent=None):
        super().__init__(parent)
        self.pool = pool
        self.devices = list(devices)
        self.rows = {d.key: i for i, d in enumerate(self.devices)}
        self.pending = set()
        self.editors = {}
        self.setWindowTitle("Kích hoạt bản quyền qua LAN — Manager CTLIOS")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.resize(1050, 490)
        layout = QVBoxLayout(self)
        help_text = QLabel("Dán key vào dòng thiết bị tương ứng rồi bấm Kích hoạt. Dùng được khi đã hết dùng thử, không cần mở màn hình iPhone.\n"
                          "iOS cần ControlIOS 4.33 trở lên. Mỗi key được cấp theo UDID và hạn dùng của thiết bị.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.table = QTableWidget(len(self.devices), 5)
        self.table.setHorizontalHeaderLabels(["Thiết bị", "UDID (bấm để copy)", "Trạng thái", "Hạn dùng", "Dán key kích hoạt"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate([165, 230, 220, 135, 250]):
            self.table.setColumnWidth(col, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, device in enumerate(self.devices):
            label = device.name or device.host
            self.table.setItem(row, 0, QTableWidgetItem(f"{label}\n{device.host}"))
            self.table.setItem(row, 1, QTableWidgetItem(""))
            self.table.setItem(row, 2, QTableWidgetItem("Đang kiểm tra…"))
            self.table.setItem(row, 3, QTableWidgetItem("—"))
            editor = QPlainTextEdit()
            editor.setPlaceholderText("Dán key cho thiết bị này…")
            self.table.setCellWidget(row, 4, editor)
            self.editors[device.key] = editor
            self.table.setRowHeight(row, 82)
        self.table.cellClicked.connect(self._copy_udid)
        layout.addWidget(self.table, 1)
        self.summary = QLabel("Key không được lưu trên PC. Khi kích hoạt thành công, Manager sẽ thử kết nối lại.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Kiểm tra trạng thái")
        self.refresh_button.clicked.connect(self.refresh)
        self.activate_button = QPushButton("Kích hoạt các dòng đã nhập key")
        self.activate_button.clicked.connect(self.activate)
        close = QPushButton("Đóng")
        close.clicked.connect(self.close)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.activate_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._result.connect(self._receive)
        self.refresh()

    def _copy_udid(self, row, column):
        if column != 1:
            return
        udid = self.table.item(row, 1).text()
        if udid:
            QApplication.clipboard().setText(udid)
            self.summary.setText("Đã copy UDID để cấp key cho thiết bị này.")

    def _busy(self):
        self.refresh_button.setEnabled(not self.pending)
        self.activate_button.setEnabled(not self.pending)
        for editor in self.editors.values():
            editor.setReadOnly(bool(self.pending))

    def _start(self, key, license_key=None):
        row = self.rows[key]
        activating = license_key is not None
        self.pending.add(key)
        self.table.item(row, 2).setText("Đang kích hoạt…" if activating else "Đang kiểm tra…")
        self._busy()
        callback = lambda k, status, error: self._result.emit(k, status, error, activating)
        try:
            if activating:
                self.pool.activate_license(key, license_key, callback)
            else:
                self.pool.check_license(key, callback)
        except Exception as exc:
            self._result.emit(key, None, str(exc), activating)

    def refresh(self):
        if self.pending:
            return
        for device in self.devices:
            self._start(device.key)

    def activate(self):
        if self.pending:
            return
        entries = [(key, editor.toPlainText().strip()) for key, editor in self.editors.items()
                   if editor.toPlainText().strip()]
        if not entries:
            self.summary.setText("Hãy dán key vào dòng thiết bị muốn kích hoạt.")
            return
        self.summary.setText(f"Đang gửi key tới {len(entries)} thiết bị…")
        for key, license_key in entries:
            self._start(key, license_key)

    def _receive(self, key, status, error, activating):
        row = self.rows[key]
        self.pending.discard(key)
        item = self.table.item(row, 2)
        if error:
            item.setText(error)
            item.setToolTip(error)
        else:
            self.table.item(row, 1).setText(status.udid)
            self.table.item(row, 3).setText(status.expires_text)
            item.setText(status.description)
            item.setToolTip(status.description)
            if activating:
                self.editors[key].clear()
                self.activated.emit(key)
        self._busy()
        if not self.pending:
            self.summary.setText("Đã hoàn tất. Xem trạng thái từng dòng; key lỗi vẫn được giữ để sửa hoặc thay thế.")

    def closeEvent(self, event):
        if self.pending:
            # Signals still have a live receiver while commands finish.
            self.hide()
            event.ignore()
            return
        for editor in self.editors.values():
            editor.clear()
        super().closeEvent(event)
