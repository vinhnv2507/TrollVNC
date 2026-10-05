"""Local package selection and per-device progress; no GitHub requests."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QPlainTextEdit, QMessageBox)

from ..config import PROJECT_ROOT
from ..ios_update import inspect_package, find_latest_package


class IOSUpdateDialog(QDialog):
    _event = Signal(str, str)
    _done = Signal(str, int, object)
    completed = Signal()

    def __init__(self, pool, targets, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cập nhật ControlIOS qua LAN")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.resize(740, 500)
        self.pool, self.targets = pool, list(targets)
        self.package = None
        self.running = False
        self.settings = QSettings("ControlIOS", "ControlIOS PC")
        layout = QVBoxLayout(self)
        self.package_label = QLabel("Chưa chọn gói ControlIOS")
        self.package_label.setTextFormat(Qt.PlainText)
        self.package_label.setWordWrap(True)
        layout.addWidget(self.package_label)
        row = QHBoxLayout()
        self.choose_file = QPushButton("Chọn gói .tipa…")
        self.choose_file.clicked.connect(self._choose_file)
        self.choose_folder = QPushButton("Lấy bản mới nhất trong thư mục…")
        self.choose_folder.clicked.connect(self._choose_folder)
        row.addWidget(self.choose_file)
        row.addWidget(self.choose_folder)
        layout.addLayout(row)
        self.info = QLabel(
            f"Cập nhật {len(self.targets)} máy đã chọn qua server tạm trên PC. Giữ PC mở đến khi xong.\n"
            "iOS 4.18 trở lên: tự tải, cài và bật lại dịch vụ. Máy bản cũ có thể cần xác nhận\n"
            "Install trong TrollStore và mở ControlIOS một lần khi nâng cấp đầu tiên.")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        layout.addWidget(self.log, 1)
        self.status = QLabel("Sẵn sàng")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.start_button = QPushButton("Cập nhật máy đã chọn")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self._event.connect(self._append)
        self._done.connect(self._finished)
        self._load_default()

    def _load_default(self):
        path = self.settings.value("ios_update/package", "")
        candidates = []
        if path:
            try:
                candidates.append(inspect_package(path))
            except (OSError, ValueError):
                pass
        folder = self.settings.value("ios_update/folder", "")
        if folder:
            latest = find_latest_package(Path(folder))
            if latest:
                candidates.append(latest)
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else PROJECT_ROOT
        packaged = find_latest_package(base / "ios")
        if packaged:
            candidates.append(packaged)
        if candidates:
            from ..ios_update import version_key
            self._select(max(candidates, key=lambda p: version_key(p.version)))

    def _select(self, package):
        self.package = package
        self.settings.setValue("ios_update/package", str(package.path))
        self.settings.setValue("ios_update/folder", str(package.path.parent))
        self.package_label.setText(f"ControlIOS {package.version} · {package.size / 1024 / 1024:.1f} MB\n{package.path}")
        self.start_button.setEnabled(True)

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn ControlIOS iOS", "", "ControlIOS (*.tipa *.ipa)")
        if path:
            try:
                self._select(inspect_package(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Gói không hợp lệ", str(exc))

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Thư mục chứa các gói ControlIOS")
        if path:
            package = find_latest_package(Path(path))
            if package:
                self._select(package)
            else:
                QMessageBox.information(self, "Chưa có gói", "Không thấy gói ControlIOS hợp lệ trong thư mục này.")

    def _start(self):
        if not self.package or self.running:
            return
        self.log.clear()
        try:
            self.pool.update_ios(self.targets, self.package,
                                 on_event=self._event.emit, on_done=self._done.emit)
        except Exception as exc:
            QMessageBox.warning(self, "Không bắt đầu được", str(exc))
            return
        self.running = True
        self.choose_file.setEnabled(False)
        self.choose_folder.setEnabled(False)
        self.start_button.setEnabled(False)
        self.close_button.setText("Ẩn (tiếp tục cập nhật)")
        self.status.setText(f"Đang cập nhật {len(self.targets)} máy…")

    def _append(self, key, message):
        self.log.appendPlainText(f"[{key}] {message}")

    def _finished(self, describe, ok, failures):
        self.running = False
        self.choose_file.setEnabled(True)
        self.choose_folder.setEnabled(True)
        self.start_button.setEnabled(True)
        self.close_button.setText("Đóng")
        self.status.setText(f"{describe}: {ok}/{len(self.targets)} máy thành công hoặc đã có bản mới; {len(failures)} máy lỗi")
        for key, reason in failures:
            self._append(key, f"LỖI {reason}")
        self.completed.emit()
