"""Control IOS — main window."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

from ..config import DEFAULT_PORT, DEFAULT_REGISTRY, DeviceSpec, Registry
from ..scan import arp_hosts, probe_hosts
from ..vnc.pool import DevicePool
from ..vnc.session import Frame, State, Tier
from .detail import DetailView
from .grid import DeviceGrid

log = logging.getLogger(__name__)

PAGE_SIZES = [("50 máy", 50), ("100 máy", 100), ("250 máy", 250), ("Tất cả", 0)]


class Bridge(QObject):
    """Carries callbacks from the asyncio thread onto the Qt thread."""

    frame = Signal(object)
    status = Signal(str, object, str)


class ScanWorker(QThread):
    found = Signal(list)
    progress = Signal(int, int)

    def __init__(self, targets: List[str], port: int, use_arp: bool, parent=None) -> None:
        super().__init__(parent)
        self.targets = targets
        self.port = port
        self.use_arp = use_arp

    def run(self) -> None:
        hosts: List[str] = []
        if self.use_arp:
            hosts.extend(arp_hosts())
        for target in self.targets:
            if "/" in target:
                import ipaddress
                hosts.extend(
                    str(ip) for ip in ipaddress.ip_network(target, strict=False).hosts()
                )
            elif "-" in target:
                base, _, last = target.rpartition("-")
                prefix, _, first = base.rpartition(".")
                hosts.extend(f"{prefix}.{i}" for i in range(int(first), int(last) + 1))
            elif target:
                hosts.append(target)

        hosts = list(dict.fromkeys(hosts))
        emit_progress = lambda done, total, hit: self.progress.emit(done, total)
        result = asyncio.run(probe_hosts(hosts, self.port, progress=emit_progress))
        self.found.emit(result)


class ScanDialog(QDialog):
    def __init__(self, port: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quét tìm máy TrollVNC")
        self.resize(460, 260)
        self.hosts: List[str] = []
        self._worker: ScanWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Dải quét (mỗi dòng một mục): 172.30.4.0/24, 172.30.4.10-90, hoặc IP đơn"
        ))
        self.targets = QPlainTextEdit("172.30.4.0/24")
        layout.addWidget(self.targets)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cổng:"))
        self.port = QLineEdit(str(port))
        self.port.setFixedWidth(70)
        row.addWidget(self.port)
        self.use_arp = QCheckBox("Dùng bảng ARP (nhanh, chỉ máy đã liên lạc)")
        self.use_arp.setChecked(True)
        row.addWidget(self.use_arp)
        row.addStretch(1)
        layout.addLayout(row)

        self.status = QLabel("")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.scan_button = buttons.addButton("Quét", QDialogButtonBox.ActionRole)
        buttons.addButton(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        self.scan_button.clicked.connect(self._start)
        layout.addWidget(buttons)

    def _start(self) -> None:
        targets = [l.strip() for l in self.targets.toPlainText().splitlines() if l.strip()]
        self.scan_button.setEnabled(False)
        self.status.setText("Đang quét...")
        self._worker = ScanWorker(targets, int(self.port.text()), self.use_arp.isChecked())
        self._worker.progress.connect(
            lambda done, total: self.status.setText(f"Đã dò {done}/{total}")
        )
        self._worker.found.connect(self._done)
        self._worker.start()

    def _done(self, hosts: List[str]) -> None:
        self.hosts = hosts
        self.status.setText(f"Tìm thấy {len(hosts)} máy TrollVNC.")
        self.scan_button.setEnabled(True)
        if hosts:
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self, registry_path: Path = DEFAULT_REGISTRY) -> None:
        super().__init__()
        self.setWindowTitle("Control IOS")
        self.resize(1500, 950)
        self.registry_path = registry_path
        self.registry = Registry.load(registry_path)

        self.page_size = 100
        self.page = 0
        self.broadcast = False

        self.bridge = Bridge()
        self.pool = DevicePool(
            self.registry.settings,
            on_frame=self.bridge.frame.emit,
            on_status=lambda k, s, d: self.bridge.status.emit(k, s, d),
        )

        self.grid = DeviceGrid(tile_width=150)
        self.detail = DetailView()
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.grid)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self._build_toolbar()
        self.setStatusBar(QStatusBar())
        self.stats_label = QLabel("")
        self.statusBar().addPermanentWidget(self.stats_label)

        self.bridge.frame.connect(self._on_frame)
        self.bridge.status.connect(self._on_status)
        self.grid.tiers_changed.connect(self.pool.set_tiers)
        self.grid.device_activated.connect(self._focus_device)
        self.grid.selection_changed.connect(self._on_selection)

        self.detail.drag_start.connect(lambda x, y: self._input("down", x, y))
        self.detail.drag_move.connect(lambda x, y: self._input("move", x, y))
        self.detail.drag_end.connect(lambda x, y: self._input("up", x, y))
        self.detail.text_typed.connect(self._type_text)
        self.detail.key_pressed.connect(self._press_key)

        self.pool.start()
        self._apply_page()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(1000)

    # ---------------------------------------------------------------- toolbar

    def _build_toolbar(self) -> None:
        bar = QToolBar("Chính")
        bar.setMovable(False)
        self.addToolBar(bar)

        scan = QAction("Quét mạng", self)
        scan.triggered.connect(self._scan)
        bar.addAction(scan)

        load = QAction("Nạp danh sách…", self)
        load.triggered.connect(self._load_list)
        bar.addAction(load)

        bar.addSeparator()
        bar.addWidget(QLabel(" Trang: "))
        self.page_combo = QComboBox()
        for label, size in PAGE_SIZES:
            self.page_combo.addItem(label, size)
        self.page_combo.setCurrentIndex(1)
        self.page_combo.currentIndexChanged.connect(self._on_page_size)
        bar.addWidget(self.page_combo)

        self.prev_button = QPushButton("◀")
        self.prev_button.clicked.connect(lambda: self._go_page(self.page - 1))
        bar.addWidget(self.prev_button)
        self.page_label = QLabel(" 1/1 ")
        bar.addWidget(self.page_label)
        self.next_button = QPushButton("▶")
        self.next_button.clicked.connect(lambda: self._go_page(self.page + 1))
        bar.addWidget(self.next_button)

        bar.addSeparator()
        select_all = QAction("Chọn tất cả", self)
        select_all.triggered.connect(self.grid.select_all)
        bar.addAction(select_all)
        clear = QAction("Bỏ chọn", self)
        clear.triggered.connect(self.grid.clear_selection)
        bar.addAction(clear)

        bar.addSeparator()
        self.broadcast_box = QCheckBox("Gửi thao tác tới các máy đã chọn")
        self.broadcast_box.toggled.connect(self._set_broadcast)
        bar.addWidget(self.broadcast_box)

    # ------------------------------------------------------------------ paging

    def _pages(self) -> int:
        if not self.page_size:
            return 1
        return max(1, (len(self.registry.devices) + self.page_size - 1) // self.page_size)

    def _page_devices(self) -> List[DeviceSpec]:
        devices = [d for d in self.registry.devices if d.enabled]
        if not self.page_size:
            return devices
        start = self.page * self.page_size
        return devices[start:start + self.page_size]

    def _apply_page(self) -> None:
        devices = self._page_devices()
        self.grid.set_devices(devices)
        self.pool.set_devices(devices)
        self.detail.set_device(None)
        self.grid.set_focus_key(None)
        self.page_label.setText(f" {self.page + 1}/{self._pages()} ")
        self.prev_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page + 1 < self._pages())

    def _on_page_size(self, index: int) -> None:
        self.page_size = self.page_combo.itemData(index)
        self.page = 0
        self._apply_page()

    def _go_page(self, page: int) -> None:
        if 0 <= page < self._pages():
            self.page = page
            self._apply_page()

    # ------------------------------------------------------------------ device

    def _scan(self) -> None:
        dialog = ScanDialog(DEFAULT_PORT, self)
        if dialog.exec() == QDialog.Accepted and dialog.hosts:
            added = self.registry.merge_hosts(dialog.hosts, int(dialog.port.text()))
            self.registry.save(self.registry_path)
            QMessageBox.information(
                self, "Quét xong",
                f"Tìm thấy {len(dialog.hosts)} máy, thêm mới {added}.",
            )
            self._apply_page()

    def _load_list(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file danh sách IP", "", "Text (*.txt *.csv);;All files (*)"
        )
        if not path:
            return
        lines = [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines()]
        added = self.registry.merge_hosts([l for l in lines if l and not l.startswith("#")])
        self.registry.save(self.registry_path)
        QMessageBox.information(self, "Nạp danh sách", f"Thêm mới {added} máy.")
        self._apply_page()

    def _focus_device(self, key: str) -> None:
        self.detail.set_device(key)
        self.grid.set_focus_key(key)
        self.detail.setFocus()

    def _on_selection(self, keys: List[str]) -> None:
        self.statusBar().showMessage(f"Đã chọn {len(keys)} máy", 3000)

    def _set_broadcast(self, on: bool) -> None:
        self.broadcast = on

    # ------------------------------------------------------------------- input

    def _targets(self) -> List[str]:
        if self.broadcast and self.grid.selection:
            return list(self.grid.selection)
        return [self.detail.key] if self.detail.key else []

    def _input(self, kind: str, x: int, y: int) -> None:
        if not self.detail.key:
            return
        if self.broadcast and self.grid.selection:
            # Ratios, so phones with different screen sizes still match.
            fb_w, fb_h = self.detail.fb_size
            if not fb_w:
                return
            if kind == "up":
                self.pool.broadcast_tap(self.grid.selection, x / fb_w, y / fb_h)
            return
        if kind == "down":
            self.pool.mouse_down(self.detail.key, x, y)
        elif kind == "move":
            self.pool.mouse_move(self.detail.key, x, y)
        else:
            self.pool.mouse_up(self.detail.key, x, y)

    def _type_text(self, text: str) -> None:
        targets = self._targets()
        if targets:
            self.pool.type_text(targets, text)

    def _press_key(self, name: str) -> None:
        targets = self._targets()
        if targets:
            self.pool.press_keys(targets, name)

    # ------------------------------------------------------------------ frames

    def _on_frame(self, frame: Frame) -> None:
        self.grid.on_frame(frame)
        self.detail.on_frame(frame)

    def _on_status(self, key: str, state: State, detail: str) -> None:
        self.grid.on_status(key, state, detail)

    def _refresh_stats(self) -> None:
        stats = self.pool.stats()
        self.stats_label.setText(
            f"Tổng {stats['total']} phiên · online {stats['online']} · "
            f"đang kết nối {stats['connecting']} · lỗi {stats['error']} · "
            f"tắt {stats['offline']}"
        )

    def closeEvent(self, event) -> None:
        self._stats_timer.stop()
        self.pool.stop()
        super().closeEvent(event)


def run() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
