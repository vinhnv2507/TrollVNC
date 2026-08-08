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
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QStatusBar, QToolBar, QToolButton, QVBoxLayout,
    QWidget,
)

from .. import script as script_lang
from ..config import (
    DEFAULT_PORT, DEFAULT_REGISTRY, DEFAULT_SCAN_RANGE, PROJECT_ROOT,
    DeviceSpec, Registry, load_named_scripts, save_named_scripts,
)
from ..scan import arp_hosts, discover_bonjour, probe_hosts
from ..vnc.pool import DevicePool
from ..vnc.session import BRIGHTNESS_STEPS, Frame, State, Tier
from .apps_panel import AppsPanel
from .detail import DetailView
from .grid import DeviceGrid
from .quality import QualityDialog
from .ssh_console import SshConsoleDialog

log = logging.getLogger(__name__)

PAGE_SIZES = [("50 máy", 50), ("100 máy", 100), ("250 máy", 250), ("Tất cả", 0)]
# 0 cột = tự chia theo bề rộng khung.
COLUMN_CHOICES = [("Cột: tự động", 0), ("4 cột", 4), ("6 cột", 6), ("8 cột", 8),
                  ("10 cột", 10), ("12 cột", 12)]
CAPTURES_DIR = PROJECT_ROOT / "captures"

SAMPLE_SCRIPT = """\
# Toạ độ là TỈ LỆ màn hình (0..1), không phải pixel,
# nên cùng kịch bản chạy đúng trên mọi cỡ iPhone.

retry 3 1
    restartapp com.zing.zalo 2
wait 1-3
shot da-mo-app
repeat 3
    swipe 0.5 0.75 0.5 0.25 0.3
    wait 1
shot sau-khi-luot
closeapp
"""

# Bảng lệnh bấm-để-chèn: (mẫu chèn vào ô soạn, mô tả ngắn).
SCRIPT_COMMANDS = [
    ("tap 0.5 0.85", "chạm tại toạ độ tỉ lệ (x y, 0..1)"),
    ("swipe 0.5 0.8 0.5 0.2 0.4", "vuốt từ (x1 y1) tới (x2 y2) trong <giây>"),
    ("swipe 0.5 0.99 0.5 0.45 0.35 0.7", "vuốt rồi GIỮ 0.7s trước khi nhả (mở switcher)"),
    ("text {nội dung}", "gõ chữ, đủ dấu tiếng Việt"),
    ("key {tên phím}", "nhấn phím theo tên keysym (Return · BackSpace · Escape · Tab · Up…)"),
    ("wait 1.5", "chờ 1.5 giây"),
    ("wait 5-10", "chờ NGẪU NHIÊN 5–10 giây (mỗi máy một số)"),
    ("shot {hậu tố}", "chụp màn hình, file có hậu tố"),
    ("clipboard {nội dung}", "đặt clipboard máy (UTF-8) — cần TrollVNC đã vá"),
    ("savephoto {đường dẫn ảnh trên máy}", "nạp ảnh đã có trên máy vào Thư viện Ảnh"),
    ("launchapp {bundle id}", "mở app theo bundle id (kênh điều khiển)"),
    ("killapp {bundle id}", "đóng app theo bundle id"),
    ("restartapp {bundle id} 2", "đóng, chờ 2s, mở lại"),
    ("openurl {url}", "mở URL bằng app mặc định"),
    ("openurlin {bundle id} {url}", "mở URL bằng đúng app chỉ định"),
    ("wipeapp {bundle id}", "xoá dữ liệu app như cài lại (giữ keychain) — đóng app trước"),
    ("snapshot {bundle id} {tên}", "lưu bản dữ liệu app (bỏ tên = tự đặt theo giờ)"),
    ("restore {bundle id} {tên}", "khôi phục dữ liệu app về bản snapshot có tên đó"),
    ("brightness min", "độ sáng: min · max · up [nấc] · down [nấc]"),
    ("volume mute", "âm lượng: mute · up · down"),
    ("repeat 3\n    swipe 0.5 0.75 0.5 0.25 0.3\n    wait 1", "lặp khối thụt lề bên dưới N lần"),
    ("retry 3 1\n    launchapp {bundle id}", "lỗi thì thử lại tối đa N lần, cách <giây>"),
    ("home", "về màn hình chính (nút cứng)"),
    ("switcher", "mở trình chuyển app (Home hai lần)"),
    ("lock", "khoá máy (nút Power)"),
    ("spotlight", "về home rồi vuốt xuống mở ô tìm kiếm"),
    ("openapp {tên app}", "mở app qua Spotlight theo TÊN hiển thị (đủ dấu)"),
    ("closeapp", "đóng app đang mở (vào switcher, hất thẻ)"),
    ("closeall 5", "hất 5 thẻ liên tiếp trong switcher"),
    ("applibrary", "sang trang App Library"),
    ("button home", "bấm nút cứng: home · power · left <x y>"),
]

# Các cử chỉ `openapp` / `closeapp` / `applibrary` không còn nút riêng: bảng
# Ứng dụng làm việc đó tốt hơn nhiều qua bundle id. Chúng vẫn dùng được trong
# kịch bản, làm phương án dự phòng cho máy chưa cài bản TrollVNC đã vá.


def _short_reason(error: str) -> str:
    """Rút gọn lỗi thành một cụm ngắn để gộp đếm."""

    lowered = error.lower()
    if "chưa cài bản đã vá" in lowered or "không hiểu lệnh" in lowered:
        return "chưa cài bản TrollVNC đã vá"
    if "không phản hồi" in lowered:
        return "không mở cổng điều khiển (chưa vá, hoặc TrollVNC không chạy)"
    if "token" in lowered:
        return "sai token"
    return error.split("\n")[0][:60]


class Bridge(QObject):
    """Carries callbacks from the asyncio thread onto the Qt thread."""

    frame = Signal(object)
    status = Signal(str, object, str)
    message = Signal(str)          # nhật ký từ luồng mạng -> luồng giao diện
    script_done = Signal()
    apps_loaded = Signal(str, object, str)   # key, danh sách AppInfo, lỗi
    snapshots_loaded = Signal(str, object, str)  # key, danh sách Snapshot, lỗi
    autolog_loaded = Signal(str, bool, str)      # key, đang chạy, nhật ký
    color_read = Signal(str, float, float, str, str)  # key, rx, ry, "RRGGBB", lỗi (đều rỗng nếu trượt)
    clipboard_pulled = Signal(str, str, str)     # key, nội dung clipboard iOS, lỗi
    bulk_done = Signal(str, int, object)     # mô tả, số máy thành công, danh sách lỗi
    ssh_result = Signal(str, int, str)       # key, mã trả về, kết quả
    ssh_done = Signal(int, object)


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
        layout.addWidget(QLabel(f"Nội dung sẽ được gửi tới {target_count} máy:"))
        self.editor = QPlainTextEdit()
        layout.addWidget(self.editor)

        self.use_clipboard = QCheckBox(
            "Đặt vào clipboard máy (UTF-8, nhanh) thay vì gõ từng phím"
        )
        self.use_clipboard.setToolTip(
            "Đi qua kênh điều khiển của TrollVNC đã vá. Nhanh hơn nhiều và giữ "
            "đúng dấu lẫn emoji, nhưng cần control_token và bản đã vá."
        )
        layout.addWidget(self.use_clipboard)

        self._hint = QLabel(
            "Gõ được tiếng Việt có dấu. Emoji thì không — ký tự nào không gửi "
            "được sẽ bị bỏ qua và ghi vào nhật ký."
        )
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self.paste_after = QCheckBox("Dán ngay vào ô đang chọn (gửi Cmd+V)")
        self.paste_after.setVisible(False)
        layout.addWidget(self.paste_after)

        self.press_enter = QCheckBox("Nhấn Enter sau khi gõ xong")
        layout.addWidget(self.press_enter)

        def _mode_changed(clipboard: bool) -> None:
            self.paste_after.setVisible(clipboard)
            self._hint.setVisible(not clipboard)

        self.use_clipboard.toggled.connect(_mode_changed)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Gửi")
        buttons.button(QDialogButtonBox.Cancel).setText("Huỷ")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.editor.setFocus()

    def result_text(self) -> tuple[str, bool]:
        return self.editor.toPlainText(), self.press_enter.isChecked()

    def delivery(self) -> tuple[str, bool, bool]:
        """(nội dung, dùng clipboard?, dán ngay?)."""

        return (self.editor.toPlainText(),
                self.use_clipboard.isChecked(),
                self.use_clipboard.isChecked() and self.paste_after.isChecked())


class BulkResultDialog(QDialog):
    """Bảng tiến trình & kết quả từng máy cho một thao tác hàng loạt.

    Các callback ``on_event``/``on_done`` chạy trên luồng mạng nên chỉ phát signal
    (an toàn qua thread Qt); phần cập nhật giao diện chạy ở luồng chính.
    """

    _line = Signal(str, str)                 # key, message
    _finished = Signal(str, int, object)     # mô tả, số máy xong, danh sách lỗi

    def __init__(self, title: str, total: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 440)
        self._total = total
        self._seen: set[str] = set()   # đếm theo MÁY, không theo số dòng log

        layout = QVBoxLayout(self)
        self.status = QLabel(f"Đang chạy trên {total} máy…")
        layout.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.log)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Đóng")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self._line.connect(self._append_line)
        self._finished.connect(self._on_finished)

    # ---- gọi được từ luồng mạng (chỉ phát signal) ----
    def on_event(self, key: str, message: str) -> None:
        self._line.emit(key, message)

    def on_done(self, describe: str, ok: int, failures) -> None:
        self._finished.emit(describe, ok, list(failures))

    # ---- chạy ở luồng chính ----
    def _append_line(self, key: str, message: str) -> None:
        mark = "✗" if message.startswith("LỖI") else "•"
        if key and key != "*":
            self._seen.add(key)     # nhiều dòng cùng một máy vẫn tính là một
            self.status.setText(f"Đã xử lý {len(self._seen)}/{self._total} máy…")
        self.log.appendPlainText(f"{mark} {key}: {message}")

    def _on_finished(self, describe: str, ok: int, failures) -> None:
        if failures:
            self.status.setText(
                f"{describe}: xong {ok}/{self._total} máy — {len(failures)} máy lỗi"
            )
            self.log.appendPlainText("")
            self.log.appendPlainText(f"— {len(failures)} máy lỗi —")
            for key, reason in failures:
                self.log.appendPlainText(f"  ✗ {key}: {reason}")
        else:
            self.status.setText(f"{describe}: xong cả {ok}/{self._total} máy ✓")


class SnapshotDialog(QDialog):
    """Danh sách snapshot của một app (lấy từ MỘT máy) để chọn khôi phục/xoá.

    Khôi phục và Xoá áp cho **tất cả máy đang chọn** với đúng tên đã chọn — nên
    danh sách hiển thị lấy từ máy đầu tiên chỉ để chọn tên; máy nào thiếu tên đó
    sẽ báo lỗi và bị bỏ qua khi chạy hàng loạt.
    """

    def __init__(self, window, bundle_id: str, targets: List[str],
                 primary_key: str) -> None:
        super().__init__(window)
        self.window = window
        self.bundle_id = bundle_id
        self.targets = targets
        self.primary_key = primary_key
        self.setWindowTitle(f"Snapshot — {bundle_id}")
        self.resize(460, 380)

        layout = QVBoxLayout(self)
        head = f"Bản snapshot trên máy <b>{primary_key}</b>"
        if len(targets) > 1:
            head += f" · thao tác áp cho <b>{len(targets)} máy</b> đang chọn"
        info = QLabel(head)
        info.setWordWrap(True)
        layout.addWidget(info)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        self.status = QLabel("Đang tải danh sách…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #9aa4b2;")
        layout.addWidget(self.status)

        row = QDialogButtonBox()
        self.new_button = row.addButton("Lưu bản mới…", QDialogButtonBox.ActionRole)
        self.restore_button = row.addButton("Khôi phục", QDialogButtonBox.AcceptRole)
        self.delete_button = row.addButton("Xoá bản này", QDialogButtonBox.DestructiveRole)
        self.clear_button = row.addButton("Xoá tất cả", QDialogButtonBox.DestructiveRole)
        row.addButton(QDialogButtonBox.Close)
        layout.addWidget(row)

        self.new_button.clicked.connect(self._save_new)
        self.restore_button.clicked.connect(self._restore_selected)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self._clear_all)
        row.rejected.connect(self.close)

        self.restore_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.list.itemSelectionChanged.connect(self._on_selection)

        self.window.bridge.snapshots_loaded.connect(self._on_loaded)
        self.reload()

    def closeEvent(self, event) -> None:
        try:
            self.window.bridge.snapshots_loaded.disconnect(self._on_loaded)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------------ tải
    def reload(self) -> None:
        self.status.setText("Đang tải danh sách…")
        self.window.pool.list_snapshots(
            self.primary_key, self.bundle_id,
            on_done=lambda k, snaps, err: self.window.bridge.snapshots_loaded.emit(
                k, snaps, err or ""),
        )

    def _on_loaded(self, key: str, snaps, err: str) -> None:
        if key != self.primary_key:
            return
        self.list.clear()
        if err:
            self.status.setText(f"Lỗi: {err}")
            return
        if not snaps:
            self.status.setText("Chưa có bản snapshot nào. Bấm “Lưu bản mới…”.")
            return
        import datetime
        for snap in snaps:
            when = (datetime.datetime.fromtimestamp(snap.epoch).strftime("%d/%m %H:%M")
                    if snap.epoch else "—")
            item = QListWidgetItem(f"{snap.name}    ·    {when}    ·    {snap.size_mb:.1f} MB")
            item.setData(Qt.UserRole, snap.name)
            self.list.addItem(item)
        self.status.setText(f"{len(snaps)} bản. Chọn một bản rồi Khôi phục hoặc Xoá.")

    def _on_selection(self) -> None:
        has = self.list.currentItem() is not None
        self.restore_button.setEnabled(has)
        self.delete_button.setEnabled(has)

    def _selected_name(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    # ---------------------------------------------------------------- thao tác
    def _save_new(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Lưu snapshot",
            "Tên bản (để trống = tự đặt theo giờ). Không dùng dấu cách hay '/':")
        if not ok:
            return
        name = name.strip()
        self.status.setText(f"Đang lưu trên {len(self.targets)} máy…")
        self.window.pool.snapshot_app(
            self.targets, self.bundle_id, name,
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, okc, fails: (
                self.window.bridge.bulk_done.emit(d, okc, fails),
                self.reload()),
        )

    def _restore_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.warning(
            self, "Khôi phục",
            f"Thay dữ liệu hiện tại của <b>{self.bundle_id}</b> trên "
            f"<b>{len(self.targets)} máy</b> bằng bản <b>{name}</b>?<br><br>"
            "Dữ liệu hiện tại sẽ mất. Máy nào không có bản tên này sẽ bị bỏ qua.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.window.pool.restore_app(
            self.targets, self.bundle_id, name,
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, okc, fails: self.window.bridge.bulk_done.emit(d, okc, fails))
        self.status.setText(f"Đang khôi phục về “{name}”…")

    def _delete_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.warning(
            self, "Xoá snapshot",
            f"Xoá bản <b>{name}</b> của {self.bundle_id} trên "
            f"<b>{len(self.targets)} máy</b>?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.window.pool.delete_snapshot(
            self.targets, self.bundle_id, name,
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, okc, fails: (
                self.window.bridge.bulk_done.emit(d, okc, fails),
                self.reload()))
        self.status.setText(f"Đang xoá “{name}”…")

    def _clear_all(self) -> None:
        answer = QMessageBox.warning(
            self, "Xoá tất cả snapshot",
            f"Xoá <b>tất cả</b> bản snapshot của {self.bundle_id} trên "
            f"<b>{len(self.targets)} máy</b>? (Dọn luôn dữ liệu sót của bản cũ.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.window.pool.clear_snapshots(
            self.targets, self.bundle_id,
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, okc, fails: (
                self.window.bridge.bulk_done.emit(d, okc, fails),
                self.reload()))
        self.status.setText("Đang xoá tất cả snapshot…")


JS_SNIPPETS = [
    ("— Chèn lệnh JS —", ""),
    ("tap(0.5, 0.5);", "chạm điểm"),
    ("tapRegion(0.4, 0.8, 0.6, 0.95);", "chạm ngẫu nhiên trong vùng"),
    ("swipe(0.5, 0.8, 0.5, 0.3, 0.4);", "vuốt"),
    ("longPress(0.5, 0.5, 1.0);", "giữ lâu"),
    ("home();", "nút Home"),
    ("typeText(\"noi dung\");", "gõ chữ"),
    ("sleep(1);", "chờ giây"),
    ("sleep(random(1, 3));", "chờ ngẫu nhiên"),
    ("while (true) {\n  \n}", "lặp mãi"),
    ("for (let i = 0; i < 10; i++) {\n  \n}", "lặp N lần"),
    ("if (matchColor(0.5, 0.5, \"FF3B30\", 15)) {\n  \n}", "nếu đúng màu"),
    ("let p = findImage(\"/var/mobile/Media/tpl.png\");\nif (p) tap(p.x, p.y);", "tìm ảnh → chạm"),
    ("let s = ocr(0.0, 0.4, 1.0, 0.6);", "đọc chữ (OCR)"),
    ("let q = findText(\"Đăng nhập\");\nif (q) tap(q.x, q.y);", "tìm CHỮ → chạm"),
    ("tapText(\"Đăng nhập\");", "chạm vào chữ (1 dòng)"),
    ("tapImage(\"/var/mobile/Media/tpl.png\");", "tìm ảnh → chạm (1 dòng)"),
    ("waitText(\"Trang chủ\", 10);", "chờ CHỮ xuất hiện"),
    ("waitImage(\"/var/mobile/Media/tpl.png\", 10);", "chờ ẢNH xuất hiện"),
    ("tapIfColor(0.5, 0.9, \"FF3B30\", 15);", "chạm nếu đúng màu"),
    ("swipeUp();", "vuốt lên"),
    ("swipeDown();", "vuốt xuống"),
    ("swipeLeft();", "vuốt trái"),
    ("swipeRight();", "vuốt phải"),
    ("repeat(5, function(i) {\n  \n});", "lặp N lần (hàm)"),
    ("retry(3, function() {\n  return tapText(\"OK\");\n});", "thử lại tới khi thành công"),
    ("launchApp(\"com.zing.zalo\");", "mở app"),
    ("killApp(\"com.zing.zalo\");", "đóng app"),
    ("openURL(\"https://\");", "mở URL"),
    ("let r = httpGet(\"https://\");", "HTTP GET"),
    ("toast(\"noi dung\");", "thông báo trên máy"),
    ("assistiveTouch(true);", "bật AssistiveTouch"),
    ("log(\"buoc 1\");", "ghi nhật ký"),
    ("setTrace(false);", "tắt tự ghi tiến trình"),
    # ----- Biến bền / clipboard / ảnh / phím cứng -----
    ("setVar(\"dem\", (getVar(\"dem\", 0)) + 1);\nlog(\"lan chay thu \" + getVar(\"dem\"));",
     "biến BỀN qua các lần chạy"),
    ("let c = getClipboard();", "đọc clipboard"),
    ("setClipboard(\"noi dung\");", "ghi clipboard"),
    ("saveScreenshot(\"/var/mobile/Media/shot.png\");", "chụp màn ra PNG"),
    ("let t = now();  // mốc mili-giây", "thời gian hiện tại"),
    ("volumeUp();", "tăng âm lượng"),
    ("volumeDown();", "giảm âm lượng"),
    ("lockScreen();", "khoá màn (nút nguồn)"),
    # ----- Cấu trúc: hàm / nhãn / máy trạng thái (thay goto) -----
    ("function tapNeuMau(x, y, mau) {\n"
     "  if (matchColor(x, y, mau, 15)) { tap(x, y); return true; }\n"
     "  return false;\n"
     "}", "hàm: chạm nếu đúng màu"),
    ("function buoc() {\n  \n}\nbuoc();", "định nghĩa & gọi hàm"),
    ("ngoai: for (let i = 0; i < 5; i++) {\n"
     "  for (let j = 0; j < 5; j++) {\n"
     "    if (dieuKien) break ngoai;      // nhảy ra vòng ngoài\n"
     "    // continue ngoai; // sang lượt vòng ngoài\n"
     "  }\n"
     "}", "vòng lặp có NHÃN (break/continue)"),
    ("let buoc = \"start\";\n"
     "while (buoc !== \"xong\") {\n"
     "  switch (buoc) {\n"
     "    case \"start\":\n"
     "      if (matchColor(0.5, 0.2, \"1E1E1E\", 15)) buoc = \"lam\";\n"
     "      else buoc = \"cho\";\n"
     "      break;\n"
     "    case \"cho\":\n"
     "      sleep(1); buoc = \"start\";       // ~ goto start\n"
     "      break;\n"
     "    case \"lam\":\n"
     "      tap(0.5, 0.8);\n"
     "      buoc = waitColor(0.5, 0.9, \"34C759\", 10, 12) ? \"xong\" : \"start\";\n"
     "      break;\n"
     "  }\n"
     "}\nlog(\"hoan tat\");", "khung MÁY TRẠNG THÁI (thay goto/label)"),
    ("try {\n  \n} catch (e) {\n  log(\"loi: \" + e);\n}", "try/catch bắt lỗi"),
    ("switch (x) {\n  case 1:\n    break;\n  default:\n    break;\n}", "switch/case"),
    ("let arr = [[0.3,0.7],[0.5,0.7],[0.7,0.7]];\n"
     "for (let p of arr) tap(p[0], p[1]);", "mảng điểm → chạm lần lượt"),
    ("let o = JSON.parse(readFile(\"/var/mobile/cfg.json\"));", "đọc JSON từ tệp"),
]


class JsAutoClickDialog(QDialog):
    """Soạn kịch bản auto-click JavaScript, lưu thư viện, ĐẨY xuống nhiều máy và
    chạy/dừng. Kịch bản chạy TRÊN MÁY (daemon) — không cần PC nối liên tục."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Auto-click (JavaScript, chạy trên máy)")
        self.resize(640, 560)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.name_combo = QComboBox()
        self.name_combo.setMinimumWidth(200)
        self.name_combo.activated.connect(self._load_selected)
        top.addWidget(QLabel("Thư viện:"))
        top.addWidget(self.name_combo, 1)
        save_btn = QPushButton("Lưu…")
        save_btn.clicked.connect(self._save)
        top.addWidget(save_btn)
        del_btn = QPushButton("Xoá")
        del_btn.clicked.connect(self._delete)
        top.addWidget(del_btn)
        self.cmd_combo = QComboBox()
        for text, desc in JS_SNIPPETS:
            self.cmd_combo.addItem(f"{text.splitlines()[0]}  ·  {desc}" if desc else text, text)
        self.cmd_combo.activated.connect(self._insert)
        top.addWidget(self.cmd_combo, 1)
        layout.addLayout(top)

        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        self.editor.setPlainText(
            "// Ví dụ: chạm giữa-dưới mỗi 1–3 giây, lặp mãi\n"
            "while (true) {\n  tap(0.5, 0.9);\n  sleep(random(1, 3));\n}\n")
        layout.addWidget(self.editor, 1)

        pick_row = QHBoxLayout()
        self.pick_btn = QToolButton()
        self.pick_btn.setText("🎨 Lấy màu (bấm lên màn)")
        self.pick_btn.setPopupMode(QToolButton.MenuButtonPopup)
        pick_menu = QMenu(self.pick_btn)
        # (nhãn, kiểu chèn) — chọn kiểu rồi bấm 1 điểm trên khung lớn.
        for label, kind in (
            ("nếu khớp màu → chạm  · if (matchColor) tap", "tapmatch"),
            ("chờ ra màu → chạm  · if (waitColor) tap", "waittap"),
            ('matchColor(x, y, "màu", 15)  · chèn', "match"),
            ('waitColor(x, y, "màu", 10, 12)  · chèn', "wait"),
            ('getColor(x, y)  · chèn', "get"),
            ("tap(x, y)  · chèn toạ độ", "tap"),
            ("chỉ chép mã màu #RRGGBB", "hex"),
        ):
            act = QAction(label, self.pick_btn)
            act.triggered.connect(lambda _=False, k=kind: self._begin_pick(k))
            pick_menu.addAction(act)
        self.pick_btn.setMenu(pick_menu)
        # Bấm thẳng nút (không mở menu) = kiểu mặc định matchColor.
        self.pick_btn.clicked.connect(lambda: self._begin_pick("match"))
        pick_row.addWidget(self.pick_btn)
        pick_row.addStretch(1)
        lib_btn = QPushButton("⇪ Đẩy làm thư viện hàm")
        lib_btn.setToolTip("Đẩy nội dung ô soạn thành THƯ VIỆN HÀM (nạp trước mọi "
                           "kịch bản trên máy) — thêm hàm mới KHÔNG cần cài lại app.")
        lib_btn.clicked.connect(self._push_prelude)
        pick_row.addWidget(lib_btn)
        layout.addLayout(pick_row)

        self.status = QLabel("Kịch bản áp cho các máy đang chọn (cần TrollVNC đã vá).")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #9aa4b2;")
        layout.addWidget(self.status)

        layout.addWidget(QLabel("Nhật ký tiến trình (máy đầu tiên đang chọn):"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(150)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 12px; "
                                    "background: #0b0d11; color: #b8c0cc;")
        layout.addWidget(self.log_view)

        log_btns = QHBoxLayout()
        log_btns.addStretch(1)
        clear_log = QPushButton("Xoá nhật ký")
        clear_log.clicked.connect(self._clear_log)
        log_btns.addWidget(clear_log)
        export_log = QPushButton("Xuất log…")
        export_log.clicked.connect(self._export_log)
        log_btns.addWidget(export_log)
        layout.addLayout(log_btns)

        row = QDialogButtonBox()
        run = row.addButton("⬇▶ Đẩy & Chạy", QDialogButtonBox.AcceptRole)
        push = row.addButton("⬇ Chỉ đẩy", QDialogButtonBox.ActionRole)
        stop = row.addButton("■ Dừng", QDialogButtonBox.DestructiveRole)
        row.addButton(QDialogButtonBox.Close)
        run.clicked.connect(self._push_run)
        push.clicked.connect(self._push_only)
        stop.clicked.connect(self._stop)
        row.rejected.connect(self.close)
        layout.addWidget(row)

        self._reload_names()

        # Kéo nhật ký từ máy đầu tiên đang chọn mỗi ~1.2s để theo dõi tiến trình.
        self._poll_key = None
        self.window.bridge.autolog_loaded.connect(self._on_autolog)
        self.window.bridge.color_read.connect(self._on_color_read)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1200)
        self.poll_timer.timeout.connect(self._poll_log)
        self.poll_timer.start()

    def closeEvent(self, event) -> None:
        self.poll_timer.stop()
        for sig, slot in ((self.window.bridge.autolog_loaded, self._on_autolog),
                          (self.window.bridge.color_read, self._on_color_read)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().closeEvent(event)

    def _poll_log(self) -> None:
        if not self.isVisible():
            return
        targets = self.window.action_targets()
        if not targets:
            return
        self._poll_key = targets[0]
        self.window.pool.autolog(
            self._poll_key,
            on_done=lambda k, running, log: self.window.bridge.autolog_loaded.emit(k, running, log))

    def _clear_log(self) -> None:
        if self._poll_key:
            self.window.pool.clear_autolog(self._poll_key)
        self.log_view.clear()

    def _export_log(self) -> None:
        text = self.log_view.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Nhật ký trống", "Chưa có nhật ký để xuất.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Xuất nhật ký", "autoclick-log.txt", "Text (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.status.setText(f"Đã xuất log: {path}")
        except OSError as exc:
            QMessageBox.warning(self, "Lỗi", f"Không ghi được file: {exc}")

    # ------------------------------------------------- lấy màu (get color)
    def _begin_pick(self, kind: str) -> None:
        if not self.window.detail.key:
            self.status.setText("Hãy mở 1 máy ra khung điều khiển lớn trước khi lấy màu.")
            return
        self._pick_kind = kind
        self.status.setText("👉 Bấm 1 điểm trên MÀN HÌNH LỚN để lấy màu tại đó…")
        self.window.begin_color_pick(self._finish_pick)

    def _finish_pick(self, rx: float, ry: float, hexcolor: Optional[str]) -> None:
        # Màu ở PC (hexcolor) chỉ gần đúng vì khung bị thu nhỏ/nén. Hỏi THẲNG máy
        # để lấy pixel gốc — đúng cái auto-click (getColor/matchColor) dùng.
        self._pick_pc_hex = hexcolor
        key = self.window.detail.key
        if key:
            self.status.setText("Đang hỏi màu thật từ máy (tối đa 3s)…")
            self.window.pool.read_color(
                key, rx, ry,
                on_done=lambda k, hx, err: self.window.bridge.color_read.emit(
                    k, rx, ry, hx or "", err or ""))
        else:
            self._insert_color(rx, ry, hexcolor)

    def _on_color_read(self, key: str, rx: float, ry: float, hexv: str, err: str) -> None:
        # Ưu tiên màu thật từ máy; nếu máy không trả (lỗi/cũ) -> LUÔN dùng màu PC
        # để vẫn có kết quả chèn, kèm lý do để biết đường sửa.
        real = hexv or getattr(self, "_pick_pc_hex", None)
        self._insert_color(rx, ry, real, from_device=bool(hexv), err=err)

    def _insert_color(self, rx: float, ry: float, hexcolor: Optional[str],
                      from_device: bool = False, err: str = "") -> None:
        if hexcolor is None:
            self.status.setText(
                f"Không lấy được màu: {err}" if err else
                "Chưa lấy được màu (máy chưa có khung?) — thử lại.")
            return
        kind = getattr(self, "_pick_kind", "match")
        snippet = {
            "tapmatch": f'if (matchColor({rx:.3f}, {ry:.3f}, "{hexcolor}", 15)) tap({rx:.3f}, {ry:.3f})',
            "waittap": f'if (waitColor({rx:.3f}, {ry:.3f}, "{hexcolor}", 10, 15)) tap({rx:.3f}, {ry:.3f})',
            "match": f'matchColor({rx:.3f}, {ry:.3f}, "{hexcolor}", 15)',
            "wait": f'waitColor({rx:.3f}, {ry:.3f}, "{hexcolor}", 10, 12)',
            "get": f'getColor({rx:.3f}, {ry:.3f})',
            "tap": f'tap({rx:.3f}, {ry:.3f})',
            "hex": hexcolor,
        }.get(kind, f'matchColor({rx:.3f}, {ry:.3f}, "{hexcolor}", 15)')
        if from_device:
            src = "màu THẬT từ máy"
        elif err:
            src = f"màu PC (máy không trả: {err})"
        else:
            src = "màu PC (gần đúng — máy chưa hỗ trợ)"
        QApplication.clipboard().setText(snippet)
        if kind == "hex":
            self.status.setText(f"Đã chép mã màu: #{snippet}  ({src})")
            return
        cursor = self.editor.textCursor()
        prefix = "" if cursor.atBlockStart() else "\n"
        cursor.insertText(f"{prefix}{snippet};\n")
        self.editor.setTextCursor(cursor)
        self.status.setText(f"Đã chèn + chép: {snippet}  ({src})")

    def _on_autolog(self, key: str, running: bool, log: str) -> None:
        if key != self._poll_key:
            return
        # Chỉ cập nhật khi đổi, để không nhảy con trỏ cuộn liên tục.
        if log != self.log_view.toPlainText():
            at_bottom = (self.log_view.verticalScrollBar().value()
                         >= self.log_view.verticalScrollBar().maximum() - 4)
            self.log_view.setPlainText(log)
            if at_bottom:
                sb = self.log_view.verticalScrollBar()
                sb.setValue(sb.maximum())
        self.status.setText(f"{key} · {'● đang chạy' if running else '○ đã dừng'}")

    # ---------------------------------------------------------- thư viện
    def _reload_names(self) -> None:
        from ..config import DEFAULT_JS_SCRIPTS, load_named_scripts
        self._scripts = load_named_scripts(DEFAULT_JS_SCRIPTS)
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        self.name_combo.addItem("— chọn kịch bản đã lưu —", "")
        for name in sorted(self._scripts):
            self.name_combo.addItem(name, name)
        self.name_combo.blockSignals(False)

    def _load_selected(self, index: int) -> None:
        name = self.name_combo.itemData(index)
        if name and name in self._scripts:
            self.editor.setPlainText(self._scripts[name])

    def _save(self) -> None:
        from ..config import DEFAULT_JS_SCRIPTS, load_named_scripts, save_named_scripts
        name, ok = QInputDialog.getText(self, "Lưu kịch bản", "Tên kịch bản:")
        if not ok or not name.strip():
            return
        scripts = load_named_scripts(DEFAULT_JS_SCRIPTS)
        scripts[name.strip()] = self.editor.toPlainText()
        save_named_scripts(scripts, DEFAULT_JS_SCRIPTS)
        self._reload_names()
        self.name_combo.setCurrentText(name.strip())
        self.status.setText(f"Đã lưu “{name.strip()}”.")

    def _delete(self) -> None:
        from ..config import DEFAULT_JS_SCRIPTS, load_named_scripts, save_named_scripts
        name = self.name_combo.currentData()
        if not name:
            return
        scripts = load_named_scripts(DEFAULT_JS_SCRIPTS)
        scripts.pop(name, None)
        save_named_scripts(scripts, DEFAULT_JS_SCRIPTS)
        self._reload_names()
        self.status.setText(f"Đã xoá “{name}”.")

    def _insert(self, index: int) -> None:
        snippet = self.cmd_combo.itemData(index)
        if snippet:
            self.editor.insertPlainText(snippet + "\n")
        self.cmd_combo.setCurrentIndex(0)

    # ---------------------------------------------------------- đẩy/chạy
    def _targets(self):
        targets = self.window.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
        return targets

    def _push_run(self) -> None:
        targets = self._targets()
        if not targets:
            return
        self.status.setText(f"Đang đẩy & chạy trên {len(targets)} máy…")
        self.window.pool.push_and_run_autoscript(
            targets, self.editor.toPlainText(),
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.window.bridge.bulk_done.emit(d, ok, fails))

    def _push_prelude(self) -> None:
        targets = self._targets()
        if not targets:
            return
        js = self.editor.toPlainText()
        if not js.strip():
            QMessageBox.information(self, "Trống", "Ô soạn đang trống — hãy dán các "
                                    "hàm tiện ích rồi bấm đẩy làm thư viện.")
            return
        self.status.setText(f"Đang đẩy THƯ VIỆN HÀM xuống {len(targets)} máy…")
        self.window.pool.push_prelude(
            targets, js,
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.window.bridge.bulk_done.emit(d, ok, fails))

    def _push_only(self) -> None:
        targets = self._targets()
        if not targets:
            return
        self.status.setText(f"Đang đẩy xuống {len(targets)} máy…")
        self.window.pool.push_autoscript(
            targets, self.editor.toPlainText(),
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.window.bridge.bulk_done.emit(d, ok, fails))

    def _stop(self) -> None:
        targets = self._targets()
        if not targets:
            return
        self.window.pool.autoclick_stop(
            targets,
            on_event=lambda k, m: self.window.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.window.bridge.bulk_done.emit(d, ok, fails))
        self.status.setText("Đang dừng…")


class ScriptDialog(QDialog):
    """Soạn và chạy kịch bản trên các máy đang chọn."""

    apps_loaded = Signal(str, object, str)  # key, apps, error; an toàn qua thread Qt

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Kịch bản tự động")
        self.resize(720, 620)
        self.running = False
        self._app_pending: set[str] = set()
        self._app_results: dict[str, object] = {}
        self._app_errors: dict[str, str] = {}
        self.apps_loaded.connect(self._on_script_apps_loaded)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Toạ độ là TỈ LỆ màn hình (0..1). Chọn một lệnh bên dưới để chèn "
            "mẫu vào ô soạn:"
        ))

        cmd_row = QHBoxLayout()
        cmd_row.addWidget(QLabel("Chèn lệnh:"))
        self.cmd_combo = QComboBox()
        self.cmd_combo.setMinimumWidth(440)
        self.cmd_combo.setMaxVisibleItems(20)
        self.cmd_combo.addItem("— chọn lệnh để chèn —", "")
        for template, desc in SCRIPT_COMMANDS:
            first = template.splitlines()[0]
            self.cmd_combo.addItem(f"{first}   —   {desc}", template)
        self.cmd_combo.activated.connect(self._insert_command_template)
        cmd_row.addWidget(self.cmd_combo, 1)
        layout.addLayout(cmd_row)

        app_row = QHBoxLayout()
        self.load_apps_button = QPushButton("Lấy danh sách app")
        self.load_apps_button.setToolTip(
            "Hỏi tất cả máy đang chọn và hợp nhất danh sách theo bundle ID"
        )
        self.load_apps_button.clicked.connect(self._load_script_apps)
        app_row.addWidget(self.load_apps_button)
        self.app_combo = QComboBox()
        self.app_combo.setMinimumWidth(300)
        self.app_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.app_combo.setPlaceholderText("Chưa lấy danh sách app")
        app_row.addWidget(self.app_combo, 1)
        for label, command, tip in [
            ("Chèn mở", "launchapp", "Chèn lệnh mở app theo bundle ID"),
            ("Chèn đóng", "killapp", "Chèn lệnh đóng app theo bundle ID"),
            ("Chèn mở lại", "restartapp", "Chèn lệnh đóng rồi mở lại app"),
        ]:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _checked=False, cmd=command: self._insert_app_command(cmd)
            )
            app_row.addWidget(button)
        layout.addLayout(app_row)

        lib_row = QHBoxLayout()
        lib_row.addWidget(QLabel("Kịch bản đã lưu:"))
        self.script_combo = QComboBox()
        self.script_combo.setMinimumWidth(240)
        self.script_combo.setToolTip("Chọn để nạp một kịch bản đã lưu vào ô soạn")
        self.script_combo.activated.connect(self._load_saved_script)
        lib_row.addWidget(self.script_combo, 1)
        save_named = QPushButton("Lưu…")
        save_named.setToolTip("Lưu kịch bản trong ô soạn với một cái tên")
        save_named.clicked.connect(self._save_named_script)
        lib_row.addWidget(save_named)
        delete_named = QPushButton("Xoá")
        delete_named.setToolTip("Xoá kịch bản đã chọn khỏi thư viện")
        delete_named.clicked.connect(self._delete_named_script)
        lib_row.addWidget(delete_named)
        layout.addLayout(lib_row)
        self._reload_script_names()

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
        if not targets:
            text = "Chưa chọn máy nào — hãy chọn ở lưới bên trái (hoặc Chọn tất cả)."
        elif len(targets) == 1:
            text = "Sẽ chạy trên 1 máy đang chọn."
        else:
            text = (f"Sẽ chạy đồng thời trên {len(targets)} máy đang chọn "
                    "(mỗi máy một luồng riêng).")
        self.target_label.setText(text)
        self.run_button.setEnabled(bool(targets) and not self.running)

    def append(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _insert_command_template(self, _index: int) -> None:
        template = self.cmd_combo.currentData()
        if not template:
            return
        cursor = self.editor.textCursor()
        before = self.editor.toPlainText()[:cursor.position()]
        if before and not before.endswith("\n"):
            cursor.insertText("\n")
        cursor.insertText(template + "\n")
        self.editor.setTextCursor(cursor)
        self.cmd_combo.setCurrentIndex(0)      # trả về placeholder
        self.editor.setFocus()

    def _load_script_apps(self) -> None:
        targets = self.window.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn ít nhất một máy.")
            return
        if self.window._needs_control_token(targets) \
                and not self.window.registry.settings.control_token:
            QMessageBox.warning(
                self, "Thiếu control token",
                "Máy WiFi cần control_token trong config/devices.json để lấy danh "
                "sách app (máy USB thì không cần).",
            )
            return

        self._app_pending = set(targets)
        self._app_results = {}
        self._app_errors = {}
        self.app_combo.clear()
        self.app_combo.setPlaceholderText(f"Đang hỏi {len(targets)} máy…")
        self.load_apps_button.setEnabled(False)
        for key in targets:
            self.window.pool.list_apps(
                key,
                on_done=lambda k, apps, error: self.apps_loaded.emit(
                    k, apps, error or ""
                ),
            )

    def _on_script_apps_loaded(self, key: str, apps, error: str) -> None:
        if key not in self._app_pending:
            return
        self._app_pending.discard(key)
        if error:
            self._app_errors[key] = error
        else:
            self._app_results[key] = apps
        if self._app_pending:
            self.app_combo.setPlaceholderText(f"Còn {len(self._app_pending)} máy…")
            return

        # bundle id -> [AppInfo đại diện, số máy có app]. Kết quả là hợp của
        # mọi máy, nên vẫn tìm thấy app chỉ được cài trên một phần thiết bị.
        merged: dict[str, list] = {}
        for device_apps in self._app_results.values():
            for info in device_apps:
                if info.bundle_id not in merged:
                    merged[info.bundle_id] = [info, 0]
                merged[info.bundle_id][1] += 1

        total = len(self._app_results) + len(self._app_errors)
        for bundle_id, (info, count) in sorted(
            merged.items(), key=lambda item: item[1][0].display_name.lower()
        ):
            coverage = f"{count}/{total} máy"
            self.app_combo.addItem(
                f"{info.display_name} — {bundle_id} — {coverage}", bundle_id
            )
        self.load_apps_button.setEnabled(True)
        self.app_combo.setPlaceholderText("Không tìm thấy app" if not merged else "Chọn app…")
        self.append(
            f"Danh sách app: {len(merged)} bundle · "
            f"{len(self._app_results)}/{total} máy trả lời"
        )
        if self._app_errors:
            self.append(f"Không lấy được từ {len(self._app_errors)} máy:")
            for key, reason in self._app_errors.items():
                self.append(f"  ✗ {key}: {reason}")

    def _insert_app_command(self, command: str) -> None:
        bundle_id = self.app_combo.currentData()
        if not bundle_id:
            QMessageBox.information(
                self, "Chưa chọn app", "Bấm Lấy danh sách app rồi chọn một app."
            )
            return
        suffix = " 1" if command == "restartapp" else ""
        cursor = self.editor.textCursor()
        if cursor.position() and not self.editor.toPlainText()[:cursor.position()].endswith("\n"):
            cursor.insertText("\n")
        cursor.insertText(f"{command} {bundle_id}{suffix}\n")
        self.editor.setTextCursor(cursor)

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

    # ------------------------------------------------------- thư viện kịch bản

    def _reload_script_names(self, select: str = "") -> None:
        """Nạp lại danh sách tên từ config/scripts.json vào combo."""

        self._saved_scripts = load_named_scripts()
        self.script_combo.blockSignals(True)
        self.script_combo.clear()
        self.script_combo.addItem("— chọn để nạp —", "")
        for name in sorted(self._saved_scripts, key=str.lower):
            self.script_combo.addItem(name, name)
        if select:
            index = self.script_combo.findData(select)
            if index >= 0:
                self.script_combo.setCurrentIndex(index)
        self.script_combo.blockSignals(False)

    def _load_saved_script(self, _index: int) -> None:
        name = self.script_combo.currentData()
        if not name:
            return
        body = self._saved_scripts.get(name)
        if body is None:
            return
        self.editor.setPlainText(body)
        self.append(f"Đã nạp kịch bản “{name}”.")

    def _save_named_script(self) -> None:
        current = self.script_combo.currentData() or ""
        name, ok = QInputDialog.getText(
            self, "Lưu kịch bản", "Tên kịch bản:", text=current
        )
        name = name.strip()
        if not ok or not name:
            return
        if name in self._saved_scripts and QMessageBox.question(
            self, "Ghi đè", f"Kịch bản “{name}” đã có. Ghi đè?"
        ) != QMessageBox.Yes:
            return
        self._saved_scripts[name] = self.editor.toPlainText()
        try:
            save_named_scripts(self._saved_scripts)
        except OSError as exc:
            QMessageBox.warning(self, "Lỗi lưu", str(exc))
            return
        self._reload_script_names(select=name)
        self.append(f"Đã lưu kịch bản “{name}”.")

    def _delete_named_script(self) -> None:
        name = self.script_combo.currentData()
        if not name:
            QMessageBox.information(self, "Chưa chọn", "Chọn một kịch bản đã lưu để xoá.")
            return
        if QMessageBox.question(self, "Xoá kịch bản", f"Xoá “{name}”?") != QMessageBox.Yes:
            return
        self._saved_scripts.pop(name, None)
        try:
            save_named_scripts(self._saved_scripts)
        except OSError as exc:
            QMessageBox.warning(self, "Lỗi lưu", str(exc))
            return
        self._reload_script_names()
        self.append(f"Đã xoá kịch bản “{name}”.")

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
        self.ssh_console: SshConsoleDialog | None = None
        self.recording_id: str | None = None

        self.bridge = Bridge()
        self.pool = DevicePool(
            self.registry.settings,
            on_frame=self.bridge.frame.emit,
            on_status=lambda k, s, d: self.bridge.status.emit(k, s, d),
        )

        self.grid = DeviceGrid(tile_width=150)
        self.grid.set_focus_streaming(getattr(self.registry.settings, "focus_streaming", True))
        self.detail = DetailView()

        # Khung lớn có nhãn tên máy ở trên để biết đang điều khiển máy nào.
        detail_pane = QWidget()
        detail_layout = QVBoxLayout(detail_pane)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(2)
        self.detail_title = QLabel("")
        self.detail_title.setAlignment(Qt.AlignCenter)
        self.detail_title.setStyleSheet(
            "font-weight: bold; padding: 3px; background: rgba(0,0,0,0.06);"
        )
        # Không để nhãn (tên máy có thể dài) kéo giãn bề rộng khung một máy — khung
        # đó phải rộng đúng bằng một chiếc iPhone, không theo độ dài chữ.
        from PySide6.QtWidgets import QSizePolicy
        self.detail_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.detail_title.setVisible(False)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail, 1)

        # Phím thiết bị nhanh NGAY DƯỚI khung lớn: Home / Chuyển app / Khoá — luôn
        # thấy khi đang xem một máy, khỏi mở bảng Ứng dụng.
        gesture_row = QHBoxLayout()
        gesture_row.setContentsMargins(2, 0, 2, 2)
        gesture_row.setSpacing(4)
        self.device_gesture_buttons = {}
        for label, gesture, tip in [
            ("⌂ Home", "home", "Về màn hình chính (nút Home)"),
            ("⇄ Chuyển app", "switcher", "Mở trình chuyển app (bấm Home hai lần)"),
            ("⏻ Khoá", "lock", "Khoá máy (nút Power)"),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(
                lambda _checked=False, g=gesture: self._run_device_gesture(g))
            gesture_row.addWidget(btn)
            self.device_gesture_buttons[gesture] = btn

        # AssistiveTouch iOS (nút tròn nổi) — bật/tắt cho máy đang xem/chọn.
        at_button = QToolButton()
        at_button.setText("⊙ AssistiveTouch")
        at_button.setToolTip("Bật/tắt nút tròn AssistiveTouch của iOS trên máy")
        at_button.setPopupMode(QToolButton.InstantPopup)
        at_menu = QMenu(at_button)
        for label, state in [("Bật", "on"), ("Tắt", "off"), ("Đảo", "toggle")]:
            act = at_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, s=state: self._set_assistive_touch(s))
        at_button.setMenu(at_menu)
        gesture_row.addWidget(at_button)
        detail_layout.addLayout(gesture_row)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.grid)
        self.splitter.addWidget(detail_pane)
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
        self.apps_panel.wipe_requested.connect(self._wipe_app)
        self.apps_panel.snapshot_requested.connect(self._snapshot_app)
        self.apps_panel.restore_requested.connect(self._restore_app)
        # Home / Chuyển app / Khoá đã chuyển xuống dưới khung lớn.
        # Cài .ipa / Độ sáng / Đẩy file đã chuyển ra thanh công cụ chính.

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
        self.bridge.bulk_done.connect(self._on_bulk_done)
        self.bridge.ssh_result.connect(self._on_ssh_result)
        self.bridge.ssh_done.connect(self._on_ssh_done)
        self.grid.tiers_changed.connect(self.pool.set_tiers)
        self.grid.device_activated.connect(self._focus_device)
        self.grid.selection_changed.connect(self._on_selection)
        self.grid.tile_pressed.connect(self._on_tile_pressed)
        self.grid.tile_moved.connect(self._on_tile_moved)
        self.grid.tile_released.connect(self._on_tile_released)
        self.grid.tile_scrolled.connect(self._on_tile_scrolled)

        self.detail.pointer_pressed.connect(self._on_pointer_pressed)
        self.detail.pointer_moved.connect(self._on_pointer_moved)
        self.detail.pointer_released.connect(self._on_pointer_released)
        self.detail.scrolled.connect(self._on_scrolled)
        self.detail.text_typed.connect(self._type_text)
        self.detail.keys_pressed.connect(self._press_keys)
        self.detail.paste_requested.connect(self._paste_from_pc)
        self.detail.copy_requested.connect(self._copy_to_pc)
        self.bridge.clipboard_pulled.connect(self._on_clipboard_pulled)

        self.pool.start()

        # Chế độ USB: dựng lại relay cho các máy USB đã lưu (mở lại app là chạy).
        from ..usb import UsbRelayManager
        self.usb_relays = UsbRelayManager()
        usb_devices = [d for d in self.registry.devices if d.is_usb and d.udid]
        if usb_devices:
            self.usb_relays.restore(usb_devices)

        self._apply_page()
        self._fit_detail_pane()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(1000)

    # ---------------------------------------------------------------- toolbar

    def _build_toolbar(self) -> None:
        # Chia thành hai hàng theo nhiệm vụ. QToolBar không tự wrap nên dồn mọi
        # thứ vào một hàng sẽ làm Qt giấu các action cuối sau nút » trên màn
        # hình 1366/1500 px.
        navigation_bar = QToolBar("Thiết bị và bố cục")
        navigation_bar.setObjectName("navigation-toolbar")
        navigation_bar.setMovable(False)
        # Nén padding nút cho gọn -> nhiều action vừa một hàng hơn ở màn hẹp.
        navigation_bar.setStyleSheet("QToolButton { padding: 2px 5px; }")
        self.addToolBar(navigation_bar)
        bar = navigation_bar

        scan = QAction("Quét mạng", self)
        scan.triggered.connect(self._scan)
        bar.addAction(scan)

        load = QAction("Nạp file…", self)
        load.setToolTip("Nạp danh sách IP từ file txt (mỗi dòng một IP hoặc ip:port)")
        load.triggered.connect(self._load_list)
        bar.addAction(load)

        usb = QAction("Quét USB", self)
        usb.setToolTip("Tìm iPhone đang cắm USB và điều khiển qua cáp (cần tidevice)")
        usb.triggered.connect(self._scan_usb)
        bar.addAction(usb)

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

        self.grid_control_box = QCheckBox("Điều khiển lưới")
        self.grid_control_box.setToolTip(
            "Bấm và kéo thẳng vào ô nhỏ để điều khiển máy đó, khỏi phải mở "
            "khung riêng. Ctrl/Shift+bấm vẫn để chọn máy."
        )
        self.grid_control_box.toggled.connect(self.grid.set_control_enabled)
        bar.addWidget(self.grid_control_box)

        # Thao tác tệp (trước ở bảng Ứng dụng) — chia lên hàng này để không hàng
        # nào bị tràn sau nút ». (Độ sáng nằm ở hàng Thao tác bên dưới.)
        bar.addSeparator()
        install_ipa = QAction("Cài .ipa…", self)
        install_ipa.setToolTip(
            "Phục vụ file .ipa từ PC rồi nhờ TrollStore trên các máy đang chọn tải về cài")
        install_ipa.triggered.connect(self._install_ipa)
        bar.addAction(install_ipa)

        push_file = QAction("Đẩy file…", self)
        push_file.setToolTip("Chép một file từ PC sang các máy đang chọn")
        push_file.triggered.connect(self._push_file)
        bar.addAction(push_file)

        self.addToolBarBreak()
        actions_bar = QToolBar("Thao tác")
        actions_bar.setObjectName("actions-toolbar")
        actions_bar.setMovable(False)
        actions_bar.setStyleSheet("QToolButton { padding: 2px 5px; }")
        self.addToolBar(actions_bar)
        bar = actions_bar

        self.broadcast_box = QCheckBox("Phát đa máy")
        self.broadcast_box.setToolTip(
            "Gửi thao tác chuột/phím ở máy đang điều khiển tới tất cả máy đã chọn"
        )
        self.broadcast_box.toggled.connect(self._set_broadcast)
        bar.addWidget(self.broadcast_box)

        quality = QAction("Chất lượng", self)
        quality.setToolTip("Tốc độ khung hình và độ nét — áp dụng ngay")
        quality.triggered.connect(self._open_quality_dialog)
        bar.addAction(quality)

        bar.addSeparator()
        send_text = QAction("Gõ chữ", self)
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

        save_photo = QAction("Ảnh/Video", self)
        save_photo.setToolTip(
            "Đẩy ảnh hoặc video từ PC rồi nạp thẳng vào Thư viện Ảnh của các máy "
            "đang chọn. Video lạ định dạng được tự chuẩn hoá cho iOS. Cần "
            "TrollVNC đã vá và control_token"
        )
        save_photo.triggered.connect(self._push_photo)
        bar.addAction(save_photo)

        # Độ sáng + Âm lượng gộp một nút menu (một ô cho gọn thanh công cụ).
        media_button = QToolButton()
        media_button.setText("Sáng/Âm")
        media_button.setToolTip("Chỉnh độ sáng và âm lượng các máy đang chọn")
        media_button.setPopupMode(QToolButton.InstantPopup)
        media_menu = QMenu(media_button)
        media_menu.addSection("Độ sáng")
        for label, key, repeat in [
            ("▁ Tối đa (tối nhất)", "brightness_down", BRIGHTNESS_STEPS),
            ("− Giảm một nấc", "brightness_down", 1),
            ("+ Tăng một nấc", "brightness_up", 1),
            ("▔ Sáng nhất", "brightness_up", BRIGHTNESS_STEPS),
        ]:
            act = media_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, k=key, r=repeat: self._send_media_key(k, r))
        media_menu.addSection("Âm lượng")
        for label, key, repeat in [
            ("🔊 Tăng một nấc", "volume_up", 1),
            ("🔉 Giảm một nấc", "volume_down", 1),
            ("🔈 Giảm nhiều", "volume_down", 5),
            ("🔇 Tắt tiếng", "mute", 1),
        ]:
            act = media_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, k=key, r=repeat: self._send_media_key(k, r))
        media_button.setMenu(media_menu)
        bar.addWidget(media_button)

        respring = QAction("Respring", self)
        respring.setToolTip(
            "Khởi động lại SpringBoard trên các máy đang chọn — gỡ giao diện treo, "
            "không mất jailbreak"
        )
        respring.triggered.connect(self._respring_selected)
        bar.addAction(respring)

        self.apps_action = self.apps_dock.toggleViewAction()
        self.apps_action.setText("Ứng dụng")
        self.apps_action.setToolTip(
            "Danh sách app đã cài và các thao tác trên máy — cần TrollVNC đã vá "
            "và control_token trong cấu hình"
        )
        bar.addAction(self.apps_action)

        ssh_action = QAction("SSH…", self)
        ssh_action.setToolTip("Chạy lệnh shell trên các máy đã jailbreak")
        ssh_action.triggered.connect(self._open_ssh_console)
        bar.addAction(ssh_action)

        script_button = QToolButton()
        script_button.setText("Kịch bản")
        script_button.setToolTip("Kịch bản tự động")
        script_button.setPopupMode(QToolButton.InstantPopup)
        script_menu = QMenu(script_button)
        act_pc = script_menu.addAction("Kịch bản (PC gõ qua VNC)…")
        act_pc.triggered.connect(self._open_script_dialog)
        act_js = script_menu.addAction("Auto-click JS (chạy trên máy)…")
        act_js.triggered.connect(self._open_js_autoclick)
        script_button.setMenu(script_menu)
        bar.addWidget(script_button)

        open_folder = QAction("Thư mục", self)
        open_folder.setToolTip("Mở thư mục captures chứa ảnh/ghi hình/kịch bản")
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
        self._update_detail_title(None)
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

    def _scan_usb(self) -> None:
        """Tìm iPhone cắm USB, dựng relay và nạp vào lưới (chạy qua cáp)."""

        from ..usb import list_usb_devices, tidevice_available, UsbRelayManager
        if not tidevice_available():
            QMessageBox.warning(
                self, "Không dùng được USB",
                "Chưa nói chuyện được với usbmuxd. Kiểm tra: Apple Mobile Device "
                "Support (cài iTunes) đang chạy, và dây cáp đã cắm.",
            )
            return

        devices = list_usb_devices()
        if not devices:
            QMessageBox.information(
                self, "Quét USB",
                "Không thấy iPhone nào cắm USB. Kiểm tra dây, và bấm Tin cậy trên máy.",
            )
            return

        # Dựng lại từ đầu: tắt relay cũ, bỏ máy USB cũ, thêm máy đang cắm.
        self.usb_relays.stop()
        self.usb_relays = UsbRelayManager()
        self.registry.devices = [d for d in self.registry.devices if not d.is_usb]

        specs = self.usb_relays.start_for(devices)
        for s in specs:
            self.registry.devices.append(DeviceSpec(
                host=s["host"], port=s["port"], name=s["name"], group=s["group"],
                control_port=s["control_port"], ssh_port=s["ssh_port"], udid=s["udid"],
            ))
        self.registry.save(self.registry_path)
        self._apply_page()
        QMessageBox.information(
            self, "Quét USB",
            f"Tìm thấy {len(devices)} máy USB, đã dựng relay và nạp vào lưới.\n"
            "Ô có nhóm 'usb', kết nối qua cáp (không cần WiFi).",
        )

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
        self._update_detail_title(key)
        # Buộc refit ở khung kế: nếu không, frame đầu có tỉ lệ trùng _detail_aspect
        # cũ sẽ bị bỏ qua và khung không co lại đúng cỡ máy mới mở.
        self._detail_aspect = -1.0
        self._fit_detail_pane()

    def _device_name(self, key: str) -> str:
        for device in self.registry.devices:
            if device.key == key:
                return device.name or device.host
        return key or ""

    def _reset_detail_view(self, key: str) -> None:
        """Xoá sạch khung lớn rồi mở lại — bỏ pixel cũ + re-promote tier để có
        khung LIVE mới sau khi framebuffer đổi cỡ."""
        if self.detail.key != key:
            return
        self.detail.set_device(None)      # xoá ảnh cũ (hết lồng)
        self._focus_device(key)           # mở lại: re-promote tier + buộc refit

    def _update_detail_title(self, key: Optional[str]) -> None:
        if key:
            self.detail_title.setText(f"🖥  {self._device_name(key)}   ·   {key}")
            self.detail_title.setVisible(True)
        else:
            self.detail_title.clear()
            self.detail_title.setVisible(False)

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
        # Nhãn "thao tác áp cho N máy" phải theo kịp, nếu không nó đứng ở con số
        # lúc nạp danh sách và người dùng tưởng đang thao tác một máy.
        self.apps_panel.set_targets(len(self.action_targets()))

    def _set_broadcast(self, on: bool) -> None:
        self.broadcast = on

    # ------------------------------------------- chụp ảnh / ghi hình / kịch bản

    def action_targets(self) -> List[str]:
        """Máy để chạy hàng loạt: đang chọn, không thì máy đang mở full."""

        if self.grid.selection:
            return list(self.grid.selection)
        return [self.detail.key] if self.detail.key else []

    def _needs_control_token(self, keys) -> bool:
        """Có máy WiFi trong nhóm không? Máy USB (loopback) không cần token."""

        usb_keys = {d.key for d in self.registry.devices if d.is_usb}
        return any(key not in usb_keys for key in keys)

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

    def _open_quality_dialog(self) -> None:
        s0 = self.registry.settings
        old_scale = s0.device_scale
        old_smooth = (s0.device_low_latency, s0.device_orientation_sync)
        dialog = QualityDialog(self.registry.settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        dialog.apply()
        # Phiên đọc Settings ở mỗi vòng nhịp nên đổi là ăn ngay, khỏi nối lại.
        self.registry.save(self.registry_path)
        settings = self.registry.settings
        self.grid.set_focus_streaming(settings.focus_streaming)

        # Độ mượt (Q/defer/xoay) đi qua control socket — KHÔNG resize nên áp thẳng
        # lên máy online, không nối lại. Chỉ phát khi đổi.
        if (settings.device_low_latency, settings.device_orientation_sync) != old_smooth:
            targets = self.pool.online_keys()
            if targets:
                inflight = 1 if settings.device_low_latency else 2
                defer = 0.008 if settings.device_low_latency else 0.015
                self.pool.set_smoothness(
                    targets, inflight, defer, settings.device_orientation_sync,
                    on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
                    on_done=lambda d, ok, fails: self.bridge.bulk_done.emit(d, ok, fails))

        # Scale đi qua control socket của từng máy (không phải Settings phía PC),
        # nên phải phát riêng — và chỉ khi thật sự đổi để tránh nối lại vô cớ.
        if abs(settings.device_scale - old_scale) > 1e-6:
            # Gửi cho mọi máy đang online: máy USB (loopback) đổi được ngay; máy
            # WiFi thiếu token sẽ hiện lỗi rõ ngay trong bảng kết quả — không cần
            # popup cảnh báo riêng, tránh làm phiền mỗi lần đổi scale.
            targets = self.pool.online_keys()
            if targets:
                dlg = BulkResultDialog(
                    f"Đặt scale {settings.device_scale:.2f}", len(targets), self)
                dlg.show()
                self.pool.set_scale(targets, settings.device_scale,
                                    on_event=dlg.on_event, on_done=dlg.on_done)
                # Khung lớn giữ pixel cũ khi framebuffer đổi cỡ -> reset hẳn sau khi
                # phiên nối lại (xoá ảnh cũ, buộc refit) để hết lồng/quá màn.
                if self.detail.key and self.detail.key in targets:
                    key = self.detail.key
                    QTimer.singleShot(4000, lambda k=key: self._reset_detail_view(k))

        self.statusBar().showMessage(
            f"Đã áp dụng: {settings.live_fps:g} hình/giây · "
            f"{'gốc' if not settings.live_long_edge else str(settings.live_long_edge) + 'px'}"
            f" · scale {settings.device_scale:.2f}",
            6000,
        )

    def _send_media_key(self, name: str, repeat: int) -> None:
        """Độ sáng/âm lượng — đi qua VNC nên máy chưa vá cũng dùng được."""

        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        self.pool.media_key(targets, name, repeat)
        label = "giảm" if "down" in name else "tăng"
        self.statusBar().showMessage(
            f"{label} độ sáng {repeat} nấc trên {len(targets)} máy", 4000
        )

    def _install_ipa(self) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file .ipa để cài", "", "iOS app (*.ipa *.tipa);;All files (*)"
        )
        if not path:
            return

        ipa = Path(path)
        answer = QMessageBox.question(
            self, "Cài app hàng loạt",
            f"Cài <b>{ipa.name}</b> ({ipa.stat().st_size // 1024} KB) lên "
            f"<b>{len(targets)} máy</b>?<br><br>"
            "PC sẽ mở một web server tạm để máy tải file về. TrollStore trên "
            "từng máy có thể hỏi xác nhận — lúc đó bấm OK qua màn hình VNC.",
        )
        if answer != QMessageBox.Yes:
            return

        self.apps_dock.show()
        self.pool.install_ipa(
            targets, ipa,
            on_event=lambda k, m: self.bridge.message.emit(f"[{k or 'PC'}] {m}"),
        )
        self.statusBar().showMessage(f"Đang cài {ipa.name} lên {len(targets)} máy", 8000)

    def _push_file(self) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return

        path, _ = QFileDialog.getOpenFileName(self, "Chọn file cần đẩy sang máy")
        if not path:
            return

        remote, ok = QInputDialog.getText(
            self, "Đường dẫn trên máy",
            "Đường dẫn tuyệt đối trên iPhone:",
            text=f"/var/mobile/Documents/{Path(path).name}",
        )
        if not ok or not remote.strip():
            return

        self.pool.push_file(
            targets, Path(path), remote.strip(),
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
        )
        self.statusBar().showMessage(
            f"Đang đẩy {Path(path).name} tới {len(targets)} máy", 6000
        )

    def _push_photo(self) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        if self._needs_control_token(targets) and not self.registry.settings.control_token:
            QMessageBox.warning(
                self, "Thiếu control token",
                "Máy WiFi cần control_token trong config/devices.json để nạp ảnh "
                "(máy USB thì không cần).",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn ảnh hoặc video cần nạp vào Thư viện",
            filter=("Ảnh & video (*.png *.jpg *.jpeg *.heic *.gif *.mp4 *.mov "
                    "*.m4v *.mkv *.avi *.webm);;Tất cả (*.*)"),
        )
        if not path:
            return

        dialog = BulkResultDialog(f"Nạp {Path(path).name}", len(targets), self)
        dialog.show()
        self.pool.push_photo(
            targets, Path(path),
            on_event=dialog.on_event, on_done=dialog.on_done,
        )
        self.statusBar().showMessage(
            f"Đang nạp {Path(path).name} vào Thư viện của {len(targets)} máy", 6000
        )

    def _respring_selected(self) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        if self._needs_control_token(targets) and not self.registry.settings.control_token:
            QMessageBox.warning(self, "Thiếu control token",
                                "Máy WiFi cần control_token trong config/devices.json "
                                "(máy USB thì không cần).")
            return
        if QMessageBox.question(
            self, "Respring",
            f"Khởi động lại SpringBoard trên {len(targets)} máy?\n"
            "Gỡ giao diện treo, không mất jailbreak.",
        ) != QMessageBox.Yes:
            return
        dialog = BulkResultDialog("Respring", len(targets), self)
        dialog.show()
        self.pool.respring(targets, on_event=dialog.on_event, on_done=dialog.on_done)
        self.statusBar().showMessage(f"Đang respring {len(targets)} máy", 5000)

    def _run_device_gesture(self, gesture: str) -> None:
        """Nút Home / Chuyển app / Khoá trong bảng Ứng dụng.

        Đây là thao tác mức thiết bị nên vẫn đi bằng cử chỉ (nút cứng qua map
        nút chuột của TrollVNC), không qua kênh điều khiển.
        """

        labels = {"home": "Về màn hình chính", "switcher": "Trình chuyển app",
                  "lock": "Khoá máy"}
        self._run_quick_action(labels.get(gesture, gesture), gesture, False)

    def _set_assistive_touch(self, state: str) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn/mở một máy.")
            return
        self.pool.assistive_touch(
            targets, state,
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.bridge.bulk_done.emit(d, ok, fails))

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
        self.apps_panel.set_busy(f"Đang mở {bundle_id} trên {len(targets)} máy…")
        self.pool.launch_app(
            targets, bundle_id,
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.bridge.bulk_done.emit(d, ok, fails),
        )

    def _terminate_app(self, bundle_id: str) -> None:
        targets = self.action_targets()
        if not targets:
            return
        self.apps_panel.set_busy(f"Đang đóng {bundle_id} trên {len(targets)} máy…")
        self.pool.terminate_app(
            targets, bundle_id,
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.bridge.bulk_done.emit(d, ok, fails),
        )

    def _wipe_app(self, bundle_id: str) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        answer = QMessageBox.warning(
            self, "Xoá dữ liệu app",
            f"Xoá sạch dữ liệu của <b>{bundle_id}</b> trên <b>{len(targets)} máy</b> "
            "(Documents/Library/tmp), như vừa cài lại?<br><br>"
            "App sẽ được đóng trước. Keychain KHÔNG bị đụng — token cũ vẫn còn. "
            "Thao tác không hoàn tác được (trừ khi bạn đã lưu snapshot).",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.apps_panel.set_busy(f"Đang xoá dữ liệu {bundle_id} trên {len(targets)} máy…")
        self.pool.wipe_app(
            targets, bundle_id,
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.bridge.bulk_done.emit(d, ok, fails),
        )

    def _snapshot_app(self, bundle_id: str) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        name, ok = QInputDialog.getText(
            self, "Lưu snapshot",
            f"Tên bản snapshot cho {bundle_id} (để trống = tự đặt theo giờ).\n"
            "Không dùng dấu cách hay '/':")
        if not ok:
            return
        name = name.strip()
        self.apps_panel.set_busy(f"Đang lưu snapshot {bundle_id} trên {len(targets)} máy…")
        self.pool.snapshot_app(
            targets, bundle_id, name,
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
            on_done=lambda d, ok, fails: self.bridge.bulk_done.emit(d, ok, fails),
        )

    def _restore_app(self, bundle_id: str) -> None:
        """Mở trình quản lý snapshot: liệt kê các bản (từ máy đầu tiên) rồi chọn
        bản để khôi phục/xoá trên tất cả máy đang chọn."""

        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        dialog = SnapshotDialog(self, bundle_id, targets, targets[0])
        dialog.show()

    def _on_bulk_done(self, describe: str, ok: int, failures) -> None:
        total = ok + len(failures)
        if not failures:
            self.apps_panel.set_note(f"{describe}: xong trên {ok}/{total} máy")
            self.statusBar().showMessage(f"{describe}: xong trên {ok} máy", 5000)
            return

        # Gộp theo loại lỗi: 11 máy cùng một lý do thì nói một lần, đừng liệt kê 11 dòng.
        reasons: dict[str, int] = {}
        for _key, error in failures:
            reasons[_short_reason(error)] = reasons.get(_short_reason(error), 0) + 1
        detail = " · ".join(f"{count} máy {reason}" for reason, count in reasons.items())

        self.apps_panel.set_note(f"{describe}: xong {ok}/{total} máy — {detail}", error=True)
        self.statusBar().showMessage(f"{describe}: {ok}/{total} máy · {detail}", 10000)

    def _open_ssh_console(self) -> None:
        targets = self.action_targets()
        if not targets:
            QMessageBox.information(self, "Chưa chọn máy", "Hãy chọn máy ở lưới.")
            return
        if self.ssh_console is None:
            self.ssh_console = SshConsoleDialog(len(targets), self)
            self.ssh_console.run_requested.connect(self._run_ssh)
        self.ssh_console.set_targets(len(targets))
        self.ssh_console.show()
        self.ssh_console.raise_()

    def _run_ssh(self, command: str) -> None:
        targets = self.action_targets()
        if not targets:
            return
        self.pool.run_ssh(
            targets, command,
            on_result=lambda k, r, e: self.bridge.ssh_result.emit(
                k, -1 if r is None else r.exit_code, e or (r.output if r else "")
            ),
            on_done=lambda d, ok, fails: self.bridge.ssh_done.emit(ok, fails),
        )

    def _on_ssh_result(self, key: str, code: int, output: str) -> None:
        if self.ssh_console:
            self.ssh_console.add_result(key, code, output)

    def _on_ssh_done(self, ok: int, failures) -> None:
        if self.ssh_console:
            self.ssh_console.finish(ok, failures)

    def _open_script_dialog(self) -> None:
        if self.script_dialog is None:
            self.script_dialog = ScriptDialog(self)
        self.script_dialog.refresh_targets()
        self.script_dialog.show()
        self.script_dialog.raise_()

    def _open_js_autoclick(self) -> None:
        if getattr(self, "js_autoclick_dialog", None) is None:
            self.js_autoclick_dialog = JsAutoClickDialog(self)
        self.js_autoclick_dialog.show()
        self.js_autoclick_dialog.raise_()

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

    def begin_color_pick(self, callback) -> None:
        """Bật chế độ lấy màu: cú BẤM kế trên khung điều khiển lớn sẽ gọi
        callback(rx, ry, "RRGGBB") thay vì chạm — để chèn lệnh vào kịch bản."""
        self._pick_color_cb = callback
        self.detail.setCursor(Qt.CrossCursor)

    def _end_color_pick(self) -> None:
        self._pick_color_cb = None
        self.detail.unsetCursor()

    def _on_pointer_pressed(self, x: int, y: int, button: int) -> None:
        if not self.detail.key:
            return
        if getattr(self, "_pick_color_cb", None) is not None:
            cb = self._pick_color_cb
            self._end_color_pick()
            self._swallow_release = True   # nuốt cú nhả đi kèm, không gửi mouse_up
            ratios = self._ratios(x, y)
            if ratios:
                cb(ratios[0], ratios[1], self.detail.color_at_fb(x, y))
            return          # lấy màu, KHÔNG chạm
        self._press_origin = (x, y)
        self._press_time = time.monotonic()
        if self._broadcasting():
            return          # chờ tới lúc nhả mới biết là chạm hay vuốt
        self.pool.mouse_down(self.detail.key, x, y, button)

    def _on_pointer_moved(self, x: int, y: int) -> None:
        ratios = self._ratios(x, y)
        if ratios:
            text = f"x={x} y={y}  ·  {ratios[0]:.3f} {ratios[1]:.3f}"
            color = self.detail.color_at_fb(x, y)
            if color:
                text += f"  ·  #{color}"
            self.coords_label.setText(text)
        if not self.detail.key or self._broadcasting():
            return
        if self.detail._dragging:
            self.pool.mouse_move(self.detail.key, x, y)

    def _on_pointer_released(self, x: int, y: int, button: int) -> None:
        if not self.detail.key:
            return
        if getattr(self, "_swallow_release", False):
            self._swallow_release = False
            return          # cú nhả đi kèm lần lấy màu — bỏ qua
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

    # ------------------------------------------ điều khiển thẳng trên lưới

    def _tile_ratios(self, key: str, x: int, y: int):
        tile = self.grid.tiles.get(key)
        if not tile or not tile._fb[0]:
            return None
        return x / tile._fb[0], y / tile._fb[1]

    def _on_tile_pressed(self, key: str, x: int, y: int, button: int) -> None:
        self._tile_origin = (x, y)
        self._tile_time = time.monotonic()
        if self._broadcasting():
            return          # chờ tới lúc nhả mới biết là chạm hay vuốt
        self.pool.mouse_down(key, x, y, button)

    def _on_tile_moved(self, key: str, x: int, y: int) -> None:
        if not self._broadcasting():
            self.pool.mouse_move(key, x, y)

    def _on_tile_released(self, key: str, x: int, y: int, button: int) -> None:
        origin = getattr(self, "_tile_origin", None)
        if self._broadcasting():
            start = self._tile_ratios(key, *origin) if origin else None
            end = self._tile_ratios(key, x, y)
            if end is None:
                return
            moved = origin and max(abs(x - origin[0]), abs(y - origin[1]))
            if start and moved and moved > self.DRAG_THRESHOLD:
                duration = max(0.1, min(1.5, time.monotonic() - self._tile_time))
                self.pool.broadcast_swipe(self.grid.selection, start, end, duration)
            else:
                self.pool.broadcast_tap(self.grid.selection, *end)
            return
        self.pool.mouse_up(key, x, y, button)

    def _on_tile_scrolled(self, key: str, x: int, y: int, dx: int, dy: int) -> None:
        if self._broadcasting():
            ratios = self._tile_ratios(key, x, y)
            if ratios:
                self.pool.broadcast_scroll(self.grid.selection, *ratios, dx, dy)
            return
        self.pool.scroll(key, x, y, dx, dy)

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

    def _paste_from_pc(self) -> None:
        """Ctrl+V trên PC: đưa chữ trong clipboard PC xuống iOS rồi DÁN (Cmd+V)."""
        text = QApplication.clipboard().text()
        if not text:
            self.statusBar().showMessage("Clipboard PC trống — không có gì để dán.", 3000)
            return
        targets = self._targets()
        if not targets:
            return

        def _after(_desc, ok_count: int, _fails) -> None:
            # Bấm Cmd+V SAU khi clipboard iOS đã đặt xong (không thì dán nhầm cũ).
            if ok_count:
                self.pool.press_keys(targets, "Super_L", "v")

        self.pool.set_clipboard(
            targets, text,
            on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
            on_done=_after)
        self.statusBar().showMessage(f"Đang dán {len(text)} ký tự vào {len(targets)} máy…", 3000)

    def _copy_to_pc(self) -> None:
        """Ctrl+C trên PC: bảo iOS chép (Cmd+C) rồi kéo clipboard iOS về PC."""
        key = self.detail.key
        if not key:
            return
        # Cmd+C để iOS chép phần đang chọn vào clipboard iOS, rồi mới đọc về.
        self.pool.press_keys([key], "Super_L", "c")
        self.statusBar().showMessage("Đang lấy clipboard từ máy…", 2000)
        QTimer.singleShot(450, lambda k=key: self.pool.get_clipboard(
            k, on_done=lambda kk, text, err: self.bridge.clipboard_pulled.emit(
                kk, text or "", err or "")))

    def _on_clipboard_pulled(self, key: str, text: str, err: str) -> None:
        if err and not text:
            self.statusBar().showMessage(f"Không lấy được clipboard máy: {err}", 4000)
            return
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(
            f"Đã chép {len(text)} ký tự từ máy sang PC.", 4000)

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
        text, use_clipboard, paste_after = dialog.delivery()
        _, press_enter = dialog.result_text()
        if text and use_clipboard:
            if self._needs_control_token(targets) and not self.registry.settings.control_token:
                QMessageBox.warning(
                    self, "Thiếu control token",
                    "Máy WiFi cần control_token trong config/devices.json để dùng "
                    "clipboard (máy USB thì không cần).",
                )
                return
            def _after_clipboard(_desc, ok_count: int, _fails) -> None:
                # Chạy SAU khi clipboard đã đặt xong trên máy — nếu không, Cmd+V
                # bắn trước lúc clipboard kịp cập nhật và dán nhầm nội dung cũ.
                if not ok_count:
                    return
                if paste_after:
                    self.pool.press_keys(targets, "Super_L", "v")
                if press_enter:
                    self.pool.press_keys(targets, "Return")

            self.pool.set_clipboard(
                targets, text,
                on_event=lambda k, m: self.bridge.message.emit(f"[{k}] {m}"),
                on_done=_after_clipboard,
            )
        elif text:
            self.pool.type_text(targets, text, on_skipped=self._on_skipped_chars)
            if press_enter:
                self.pool.press_keys(targets, "Return")
        elif press_enter:
            self.pool.press_keys(targets, "Return")
        verb = "Đã đặt clipboard cho" if use_clipboard else "Đã gửi chữ tới"
        self.statusBar().showMessage(f"{verb} {len(targets)} máy", 4000)

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
        if key == self.detail.key:
            if state in (State.CONNECTING, State.ERROR):
                # Đang nối lại -> xoá ảnh cũ ở khung lớn để không giữ khung lồng.
                self.detail.clear_frame()
            elif state is State.ONLINE:
                # Nối lại xong -> buộc refit ở khung kế để tự co đúng cỡ mới,
                # không phải bấm đúp lại.
                self._detail_aspect = -1.0

    def _refresh_stats(self) -> None:
        stats = self.pool.stats()
        self.stats_label.setText(
            f"Tổng {stats['total']} phiên · online {stats['online']} · "
            f"đang kết nối {stats['connecting']} · ngủ {stats['dormant']} · "
            f"lỗi {stats['error']} · tắt {stats['offline']}"
        )

    def closeEvent(self, event) -> None:
        self._stats_timer.stop()
        if self.recording_id:
            self.pool.stop_recording(self.recording_id)
        self.pool.cancel_script()
        self.pool.stop()
        if getattr(self, "usb_relays", None):
            self.usb_relays.stop()   # tắt các tiến trình relay USB
        super().closeEvent(event)


def run() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
