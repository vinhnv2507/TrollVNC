"""Control IOS — main window."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStatusBar, QToolBar,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import script as script_lang
from ..config import (
    DEFAULT_PORT, DEFAULT_REGISTRY, DEFAULT_SCAN_RANGE, PROJECT_ROOT,
    DeviceSpec, Registry,
)
from ..scan import arp_hosts, discover_bonjour, probe_hosts
from ..vnc.pool import DevicePool
from ..vnc.session import Frame, State, Tier
from .apps_panel import AppsPanel
from .detail import DetailView
from .grid import DeviceGrid

log = logging.getLogger(__name__)

PAGE_SIZES = [("50 máy", 50), ("100 máy", 100), ("250 máy", 250), ("Tất cả", 0)]
# 0 cột = tự chia theo bề rộng khung.
COLUMN_CHOICES = [("Cột: tự động", 0), ("4 cột", 4), ("6 cột", 6), ("8 cột", 8),
                  ("10 cột", 10), ("12 cột", 12)]
CAPTURES_DIR = PROJECT_ROOT / "captures"

SAMPLE_SCRIPT = """\
# Toạ độ là TỈ LỆ màn hình (0..1), không phải pixel,
# nên cùng kịch bản chạy đúng trên mọi cỡ iPhone.

home
openapp Zalo
wait 2
shot da-mo-app
repeat 3
    swipe 0.5 0.75 0.5 0.25 0.3
    wait 1
shot sau-khi-luot
closeapp
"""

# (nhãn, lệnh kịch bản, có cần nhập tên app không)
QUICK_ACTIONS = [
    ("Về màn hình chính (nút Home)", "home", False),
    ("Trình chuyển app (Home ×2)", "switcher", False),
    ("Khoá máy (nút Power)", "lock", False),
    ("Mở app…", "openapp", True),
    ("Đóng app đang mở", "closeapp", False),
    ("Đóng 5 app gần đây", "closeall 5", False),
    ("Mở App Library (xem app đã cài)", "applibrary", False),
    ("Mở App Library + chụp ảnh", "applibrary\nshot app-library", False),
]


class Bridge(QObject):
    """Carries callbacks from the asyncio thread onto the Qt thread."""

    frame = Signal(object)
    status = Signal(str, object, str)
    message = Signal(str)          # nhật ký từ luồng mạng -> luồng giao diện
    script_done = Signal()
    apps_loaded = Signal(str, object, str)   # key, danh sách AppInfo, lỗi


class ScanWorker(QThread):
    found = Signal(list)
    progress = Signal(int, int)

    def __init__(self, targets: List[str], port: int, use_arp: bool,
                 use_bonjour: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.targets = targets
        self.port = port
        self.use_arp = use_arp
        self.use_bonjour = use_bonjour
        self.bonjour_found: List[str] = []

    def run(self) -> None:
        hosts: List[str] = []
        if self.use_bonjour:
            # Máy tự quảng bá thì không cần dò cổng nữa — nhận luôn.
            self.bonjour_found = discover_bonjour(prefix="")
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
        probed = asyncio.run(probe_hosts(hosts, self.port, progress=emit_progress))

        # Bonjour trả về "ip:port"; gộp lại và bỏ trùng theo địa chỉ.
        seen = {h.partition(":")[0] for h in self.bonjour_found}
        result = list(self.bonjour_found)
        result.extend(h for h in probed if h not in seen)
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
            "Dải quét (mỗi dòng một mục): 172.30.3.0/24, 172.30.3.10-90, hoặc IP đơn"
        ))
        self.targets = QPlainTextEdit(DEFAULT_SCAN_RANGE)
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

        self.use_bonjour = QCheckBox(
            "Tìm qua Bonjour — TrollVNC tự quảng bá _rfb._tcp (khuyên dùng)"
        )
        self.use_bonjour.setChecked(True)
        self.use_bonjour.setToolTip(
            "Hỏi thẳng mạng thay vì dò từng địa chỉ. Thấy được cả máy chưa từng "
            "liên lạc với PC này, và lấy đúng cổng máy đang mở."
        )
        layout.addWidget(self.use_bonjour)

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
        self.status.setText(
            "Đang tìm qua Bonjour..." if self.use_bonjour.isChecked() else "Đang quét..."
        )
        self._worker = ScanWorker(targets, int(self.port.text()),
                                  self.use_arp.isChecked(), self.use_bonjour.isChecked())
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


class SendTextDialog(QDialog):
    """Gõ một đoạn chữ vào nhiều máy cùng lúc.

    Tiện hơn hẳn việc gõ tay trong khung điều khiển khi cần nhập cùng một nội
    dung cho hàng loạt máy, và dán được từ clipboard của PC.
    """

    def __init__(self, target_count: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gõ chữ vào máy")
        self.resize(520, 300)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Nội dung sẽ được gõ vào {target_count} máy:"))
        self.editor = QPlainTextEdit()
        layout.addWidget(self.editor)
        layout.addWidget(QLabel(
            "Gõ được tiếng Việt có dấu. Emoji thì không — ký tự nào không gửi "
            "được sẽ bị bỏ qua và ghi vào nhật ký."
        ))

        self.press_enter = QCheckBox("Nhấn Enter sau khi gõ xong")
        layout.addWidget(self.press_enter)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Gửi")
        buttons.button(QDialogButtonBox.Cancel).setText("Huỷ")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.editor.setFocus()

    def result_text(self) -> tuple[str, bool]:
        return self.editor.toPlainText(), self.press_enter.isChecked()


class ScriptDialog(QDialog):
    """Soạn và chạy kịch bản trên các máy đang chọn."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Kịch bản tự động")
        self.resize(720, 620)
        self.running = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Lệnh: tap · swipe · text · key · wait · shot · repeat\n"
            "Cử chỉ iOS: home · switcher · spotlight · openapp <tên> · "
            "closeapp · closeall <số> · applibrary"
        ))
        self.editor = QPlainTextEdit(SAMPLE_SCRIPT)
        self.editor.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.editor, 3)

        self.target_label = QLabel("")
        layout.addWidget(self.target_label)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.log, 2)

        row = QHBoxLayout()
        self.check_button = QPushButton("Kiểm tra")
        self.check_button.clicked.connect(self._check)
        row.addWidget(self.check_button)
        self.open_button = QPushButton("Mở…")
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.open_button)
        self.save_button = QPushButton("Lưu…")
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        row.addStretch(1)
        self.run_button = QPushButton("Chạy")
        self.run_button.clicked.connect(self._run)
        row.addWidget(self.run_button)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.window.pool.cancel_script)
        row.addWidget(self.stop_button)
        layout.addLayout(row)

        self.refresh_targets()

    def refresh_targets(self) -> None:
        targets = self.window.action_targets()
        self.target_label.setText(
            f"Sẽ chạy song song trên {len(targets)} máy đang chọn."
            if targets else
            "Chưa chọn máy nào — hãy chọn ở lưới bên trái (hoặc Chọn tất cả)."
        )
        self.run_button.setEnabled(bool(targets) and not self.running)

    def append(self, message: str) -> None:
        self.log.appendPlainText(message)

    # ------------------------------------------------------------------ actions

    def _parse(self):
        try:
            return script_lang.parse(self.editor.toPlainText())
        except script_lang.ScriptError as exc:
            QMessageBox.warning(self, "Kịch bản sai cú pháp", str(exc))
            return None

    def _check(self) -> None:
        steps = self._parse()
        if steps is None:
            return
        total = script_lang.count_steps(steps)
        self.log.clear()
        self.append(f"Cú pháp hợp lệ · {total} lệnh sẽ chạy trên mỗi máy:")
        for line in script_lang.describe(steps):
            self.append("  " + line)

    def _run(self) -> None:
        steps = self._parse()
        if steps is None:
            return
        targets = self.window.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy",
                                    "Hãy chọn ít nhất một máy ở lưới.")
            return
        self.log.clear()
        self.append(f"Chạy trên {len(targets)} máy…")
        self.set_running(True)
        self.window.start_script(steps, targets)

    def set_running(self, running: bool) -> None:
        self.running = running
        self.run_button.setEnabled(not running and bool(self.window.action_targets()))
        self.stop_button.setEnabled(running)
        self.editor.setReadOnly(running)

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Mở kịch bản", str(CAPTURES_DIR.parent), "Kịch bản (*.txt);;All files (*)"
        )
        if path:
            self.editor.setPlainText(Path(path).read_text(encoding="utf-8"))

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu kịch bản", str(CAPTURES_DIR.parent / "kichban.txt"),
            "Kịch bản (*.txt)"
        )
        if path:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")

    def closeEvent(self, event) -> None:
        if self.running:
            self.window.pool.cancel_script()
        super().closeEvent(event)


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
        self.script_dialog: ScriptDialog | None = None
        self.recording_id: str | None = None

        self.bridge = Bridge()
        self.pool = DevicePool(
            self.registry.settings,
            on_frame=self.bridge.frame.emit,
            on_status=lambda k, s, d: self.bridge.status.emit(k, s, d),
        )

        self.grid = DeviceGrid(tile_width=150)
        self.detail = DetailView()
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.grid)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self.splitter)

        # Khung một máy chỉ rộng đúng một chiếc iPhone; phần dư về cho lưới.
        self._detail_aspect = self.detail.aspect
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(80)
        self._fit_timer.timeout.connect(self._fit_detail_pane)

        self.apps_panel = AppsPanel()
        self.apps_dock = QDockWidget("Ứng dụng trên máy", self)
        self.apps_dock.setWidget(self.apps_panel)
        self.apps_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, self.apps_dock)
        self.apps_dock.hide()
        self.apps_panel.refresh_requested.connect(self._reload_apps)
        self.apps_panel.launch_requested.connect(self._launch_app)
        self.apps_panel.terminate_requested.connect(self._terminate_app)

        self._build_toolbar()
        self.setStatusBar(QStatusBar())
        self.coords_label = QLabel("")
        self.coords_label.setToolTip(
            "Vị trí con trỏ: pixel và tỉ lệ. Số tỉ lệ dùng trực tiếp được cho "
            "lệnh tap/swipe trong kịch bản."
        )
        self.coords_label.setStyleSheet("font-family: Consolas, monospace;")
        self.statusBar().addPermanentWidget(self.coords_label)
        self.stats_label = QLabel("")
        self.statusBar().addPermanentWidget(self.stats_label)

        self.bridge.frame.connect(self._on_frame)
        self.bridge.status.connect(self._on_status)
        self.bridge.message.connect(self._on_message)
        self.bridge.script_done.connect(self._on_script_done)
        self.bridge.apps_loaded.connect(self._apply_apps)
        self.grid.tiers_changed.connect(self.pool.set_tiers)
        self.grid.device_activated.connect(self._focus_device)
        self.grid.selection_changed.connect(self._on_selection)

        self.detail.pointer_pressed.connect(self._on_pointer_pressed)
        self.detail.pointer_moved.connect(self._on_pointer_moved)
        self.detail.pointer_released.connect(self._on_pointer_released)
        self.detail.scrolled.connect(self._on_scrolled)
        self.detail.text_typed.connect(self._type_text)
        self.detail.keys_pressed.connect(self._press_keys)

        self.pool.start()
        self._apply_page()
        self._fit_detail_pane()

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

        self.columns_combo = QComboBox()
        for label, count in COLUMN_CHOICES:
            self.columns_combo.addItem(label, count)
        self.columns_combo.setToolTip(
            "Số cột của lưới. Ô tự giãn cho vừa khít bề rộng."
        )
        self.columns_combo.currentIndexChanged.connect(
            lambda index: self.grid.set_columns(self.columns_combo.itemData(index))
        )
        bar.addWidget(self.columns_combo)

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

        bar.addSeparator()
        send_text = QAction("Gõ chữ…", self)
        send_text.setToolTip("Gõ một đoạn chữ vào các máy đang chọn")
        send_text.triggered.connect(self._send_text_dialog)
        bar.addAction(send_text)

        for label, keys, tip in [
            ("⏎", ["Return"], "Nhấn Enter"),
            ("⌫", ["BackSpace"], "Nhấn Backspace"),
            ("Esc", ["Escape"], "Nhấn Escape"),
        ]:
            action = QAction(label, self)
            action.setToolTip(f"{tip} trên các máy đang chọn")
            action.triggered.connect(lambda _checked=False, k=keys: self._press_keys(k))
            bar.addAction(action)

        bar.addSeparator()
        shot = QAction("Chụp ảnh", self)
        shot.setToolTip("Chụp full độ phân giải các máy đang chọn")
        shot.triggered.connect(self._capture_selected)
        bar.addAction(shot)

        self.record_action = QAction("Ghi hình", self)
        self.record_action.setCheckable(True)
        self.record_action.setToolTip("Ghi chuỗi ảnh PNG của các máy đang chọn")
        self.record_action.toggled.connect(self._toggle_recording)
        bar.addAction(self.record_action)

        self.quick_button = QToolButton()
        self.quick_button.setText("Thao tác app ▾")
        self.quick_button.setToolTip(
            "Cử chỉ iOS dựng sẵn, chạy trên các máy đang chọn"
        )
        self.quick_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.quick_button)
        for label, source, needs_name in QUICK_ACTIONS:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, s=source, n=needs_name, l=label:
                self._run_quick_action(l, s, n)
            )
        self.quick_button.setMenu(menu)
        bar.addWidget(self.quick_button)

        self.apps_action = self.apps_dock.toggleViewAction()
        self.apps_action.setText("Ứng dụng")
        self.apps_action.setToolTip(
            "Danh sách app đã cài — cần TrollVNC đã vá và control_token trong cấu hình"
        )
        bar.addAction(self.apps_action)

        script_action = QAction("Kịch bản…", self)
        script_action.triggered.connect(self._open_script_dialog)
        bar.addAction(script_action)

        open_folder = QAction("Mở thư mục ảnh", self)
        open_folder.triggered.connect(self._open_captures_folder)
        bar.addAction(open_folder)

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
        self._fit_detail_pane()

    # ------------------------------------------------------------------ bố cục

    def _fit_detail_pane(self) -> None:
        total = self.splitter.width()
        if total <= 0:
            return
        if not self.detail.key:
            # Chưa mở máy nào thì đừng giữ chỗ cho một khung trống.
            want = self.detail.minimumWidth()
        else:
            want = self.detail.preferred_width()
        # Không để khung một máy ăn quá nửa cửa sổ, cũng không hẹp đến vô dụng.
        want = max(self.detail.minimumWidth(), min(want, int(total * 0.55)))
        self.splitter.setSizes([max(1, total - want), want])

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_timer.start()

    def _on_selection(self, keys: List[str]) -> None:
        self.statusBar().showMessage(f"Đã chọn {len(keys)} máy", 3000)
        if self.script_dialog:
            self.script_dialog.refresh_targets()

    def _set_broadcast(self, on: bool) -> None:
        self.broadcast = on

    # ------------------------------------------- chụp ảnh / ghi hình / kịch bản

    def action_targets(self) -> List[str]:
        """Máy để chạy hàng loạt: đang chọn, không thì máy đang mở full."""

        if self.grid.selection:
            return list(self.grid.selection)
        return [self.detail.key] if self.detail.key else []

    def _capture_selected(self) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy",
                                    "Hãy chọn máy ở lưới rồi bấm Chụp ảnh.")
            return
        folder = CAPTURES_DIR / "anh"
        self.statusBar().showMessage(f"Đang chụp {len(targets)} máy…", 5000)
        self.pool.capture(targets, folder, on_event=self._on_capture_event)

    def _on_capture_event(self, key: str, path: str | None, error: str | None) -> None:
        # Chạy trên luồng mạng — chỉ được phát signal, không đụng widget.
        self.bridge.message.emit(
            f"[chụp] {key}: {'LỖI ' + error if error else Path(path).name}"
        )

    def _toggle_recording(self, on: bool) -> None:
        if on:
            targets = self.action_targets()
            if not targets:
                self.record_action.setChecked(False)
                QMessageBox.information(self, "Chưa chọn máy",
                                        "Hãy chọn máy cần ghi hình.")
                return
            if len(targets) > 8 and QMessageBox.question(
                self, "Ghi hình nhiều máy",
                f"Bạn đang ghi {len(targets)} máy cùng lúc. Mỗi máy là một luồng "
                "ảnh full độ phân giải — sẽ nặng CPU và ổ đĩa. Tiếp tục?",
            ) != QMessageBox.Yes:
                self.record_action.setChecked(False)
                return
            folder = CAPTURES_DIR / "ghihinh"
            self.recording_id = self.pool.start_recording(
                targets, folder, fps=2.0, on_event=self._on_capture_event
            )
            self.record_action.setText("■ Dừng ghi")
            self.statusBar().showMessage(
                f"Đang ghi {len(targets)} máy vào {folder}", 8000
            )
        else:
            self.pool.stop_recording(self.recording_id)
            self.recording_id = None
            self.record_action.setText("Ghi hình")
            self.statusBar().showMessage("Đã dừng ghi hình", 5000)

    def _run_quick_action(self, label: str, source: str, needs_name: bool) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy",
                                    "Hãy chọn máy ở lưới trước khi chạy thao tác.")
            return

        if needs_name:
            name, ok = QInputDialog.getText(
                self, "Mở app",
                "Tên app đúng như hiển thị trên iPhone\n"
                "(tìm qua Spotlight, nên gõ đủ dấu):",
            )
            if not ok or not name.strip():
                return
            source = f"{source} {name.strip()}"

        try:
            steps = script_lang.parse(source)
        except script_lang.ScriptError as exc:
            QMessageBox.warning(self, "Cử chỉ hỏng",
                                f"{exc}\n\nKiểm tra lại config/gestures.json.")
            return

        self.statusBar().showMessage(f"{label} · {len(targets)} máy", 5000)
        self.start_script(steps, targets)

    # ------------------------------------------------------- bảng ứng dụng

    def _reload_apps(self) -> None:
        key = self.detail.key or (self.grid.selection[0] if self.grid.selection else None)
        if not key:
            self.apps_panel.set_error(
                "Chưa chọn máy nào. Double-click một ô ở lưới rồi bấm lại."
            )
            return
        if not self.registry.settings.control_token:
            self.apps_panel.set_error(
                "Chưa đặt control_token trong config/devices.json — không có nó thì "
                "không hỏi được máy. Xem docs/trollvnc-patch.md."
            )
            return
        self.apps_panel.set_loading()
        self.pool.list_apps(key, on_done=self._on_apps_loaded)

    def _on_apps_loaded(self, key: str, apps, error) -> None:
        # Chạy trên luồng mạng — chuyển sang luồng giao diện.
        self.bridge.apps_loaded.emit(key, apps, error or "")

    def _apply_apps(self, key: str, apps, error: str) -> None:
        if error:
            self.apps_panel.set_error(f"{key}: {error}")
            return
        self.apps_panel.set_apps(apps)
        self.apps_panel.set_targets(len(self.action_targets()))
        self.statusBar().showMessage(f"{key}: {len(apps)} app", 4000)

    def _launch_app(self, bundle_id: str) -> None:
        # Dùng máy đang CHỌN, không phải _targets() vốn chỉ trả về gì đó khi
        # bật chế độ phát thao tác.
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        self.pool.launch_app(targets, bundle_id,
                             on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"))
        self.statusBar().showMessage(f"Mở {bundle_id} trên {len(targets)} máy", 4000)

    def _terminate_app(self, bundle_id: str) -> None:
        targets = self.action_targets()
        if not targets:
            return
        self.pool.terminate_app(targets, bundle_id,
                                on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"))
        self.statusBar().showMessage(f"Đóng {bundle_id} trên {len(targets)} máy", 4000)

    def _open_script_dialog(self) -> None:
        if self.script_dialog is None:
            self.script_dialog = ScriptDialog(self)
        self.script_dialog.refresh_targets()
        self.script_dialog.show()
        self.script_dialog.raise_()

    def start_script(self, steps, targets: List[str]) -> None:
        self.pool.run_script(
            targets, steps, CAPTURES_DIR / "kichban",
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
            on_done=self.bridge.script_done.emit,
        )

    def _on_message(self, message: str) -> None:
        if self.script_dialog and self.script_dialog.isVisible():
            self.script_dialog.append(message)
        self.statusBar().showMessage(message, 4000)

    def _on_script_done(self) -> None:
        if self.script_dialog:
            self.script_dialog.set_running(False)
            self.script_dialog.append("— Kết thúc —")

    def _open_captures_folder(self) -> None:
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(CAPTURES_DIR)  # noqa: S606 — Windows-only, đường dẫn nội bộ

    # ------------------------------------------------------------------- input

    def _targets(self) -> List[str]:
        if self.broadcast and self.grid.selection:
            return list(self.grid.selection)
        return [self.detail.key] if self.detail.key else []

    # Kéo quá số pixel này thì tính là vuốt, không phải chạm.
    DRAG_THRESHOLD = 12

    def _ratios(self, x: int, y: int) -> Optional[tuple[float, float]]:
        fb_w, fb_h = self.detail.fb_size
        if not fb_w or not fb_h:
            return None
        return x / fb_w, y / fb_h

    def _on_pointer_pressed(self, x: int, y: int, button: int) -> None:
        if not self.detail.key:
            return
        self._press_origin = (x, y)
        self._press_time = time.monotonic()
        if self._broadcasting():
            return          # chờ tới lúc nhả mới biết là chạm hay vuốt
        self.pool.mouse_down(self.detail.key, x, y, button)

    def _on_pointer_moved(self, x: int, y: int) -> None:
        ratios = self._ratios(x, y)
        if ratios:
            self.coords_label.setText(
                f"x={x} y={y}  ·  {ratios[0]:.3f} {ratios[1]:.3f}"
            )
        if not self.detail.key or self._broadcasting():
            return
        if self.detail._dragging:
            self.pool.mouse_move(self.detail.key, x, y)

    def _on_pointer_released(self, x: int, y: int, button: int) -> None:
        if not self.detail.key:
            return
        origin = getattr(self, "_press_origin", None)

        if self._broadcasting():
            start = self._ratios(*origin) if origin else None
            end = self._ratios(x, y)
            if end is None:
                return
            moved = origin and max(abs(x - origin[0]), abs(y - origin[1]))
            if start and moved and moved > self.DRAG_THRESHOLD:
                # Trước đây mọi cú kéo bị co lại thành một cú chạm ở điểm nhả,
                # nên không thể vuốt hàng loạt. Giờ gửi đúng cử chỉ vuốt.
                duration = max(0.1, min(1.5, time.monotonic() - self._press_time))
                self.pool.broadcast_swipe(
                    self.grid.selection, start, end, duration
                )
                self.statusBar().showMessage(
                    f"Vuốt trên {len(self.grid.selection)} máy", 3000
                )
            else:
                self.pool.broadcast_tap(self.grid.selection, *end)
                self.statusBar().showMessage(
                    f"Chạm trên {len(self.grid.selection)} máy", 3000
                )
            return

        self.pool.mouse_up(self.detail.key, x, y, button)

    def _on_scrolled(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self.detail.key:
            return
        if self._broadcasting():
            ratios = self._ratios(x, y)
            if ratios:
                self.pool.broadcast_scroll(self.grid.selection, *ratios, dx, dy)
            return
        self.pool.scroll(self.detail.key, x, y, dx, dy)

    def _broadcasting(self) -> bool:
        return bool(self.broadcast and self.grid.selection)

    def _type_text(self, text: str) -> None:
        targets = self._targets()
        if targets:
            self.pool.type_text(targets, text, on_skipped=self._on_skipped_chars)

    def _press_keys(self, keys: List[str]) -> None:
        targets = self._targets()
        if targets:
            self.pool.press_keys(targets, *keys)

    def _on_skipped_chars(self, key: str, skipped: str) -> None:
        self.bridge.message.emit(
            f"[gõ] {key}: bỏ qua ký tự không gửi được: {skipped!r}"
        )

    def _send_text_dialog(self) -> None:
        targets = self._targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy",
                                    "Hãy chọn máy hoặc mở một máy ở khung bên phải.")
            return
        dialog = SendTextDialog(len(targets), self)
        if dialog.exec() != QDialog.Accepted:
            return
        text, press_enter = dialog.result_text()
        if text:
            self.pool.type_text(targets, text, on_skipped=self._on_skipped_chars)
        if press_enter:
            self.pool.press_keys(targets, "Return")
        self.statusBar().showMessage(f"Đã gửi chữ tới {len(targets)} máy", 4000)

    # ------------------------------------------------------------------ frames

    def _on_frame(self, frame: Frame) -> None:
        self.grid.on_frame(frame)
        self.detail.on_frame(frame)
        if frame.key == self.detail.key and self.detail.aspect != self._detail_aspect:
            # Biết tỉ lệ thật của máy (kể cả khi máy xoay ngang) -> co lại cho khít.
            self._detail_aspect = self.detail.aspect
            self._fit_timer.start()

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
        if self.recording_id:
            self.pool.stop_recording(self.recording_id)
        self.pool.cancel_script()
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
