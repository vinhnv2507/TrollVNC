"""Shopee cookie table with local persistence and background proxy checks."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from PySide6.QtCore import Qt, QThread, Signal, QItemSelectionModel, QPoint
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
)

from ..shopee import Account, AccountStore, ShopeeClient, ShopeeError, normalize_cookie, normalize_proxy, proxy_label, proxy_assignment


class CheckWorker(QThread):
    checked = Signal(str, str, object)

    def __init__(self, accounts: list[Account], parent=None):
        super().__init__(parent)
        self.accounts = copy.deepcopy(accounts)

    def _check(self, account: Account):
        client = None
        try:
            client = ShopeeClient(account.cookie, account.proxy, cancelled=self.isInterruptionRequested)
            return client.check()
        except ShopeeError as exc:
            return {"status": str(exc), "orders": [], "vouchers": [],
                    "order_error": str(exc), "voucher_error": str(exc)}
        except Exception:
            # Never expose cookies or proxy passwords from a library exception.
            return {"status": "Lỗi xử lý phản hồi Shopee.", "orders": [], "vouchers": [],
                    "order_error": "Lỗi xử lý phản hồi Shopee.", "voucher_error": "Lỗi xử lý phản hồi Shopee."}
        finally:
            if client is not None:
                client.close()

    def run(self):
        # Bound queued work too: cancellation does not start more account requests.
        accounts = iter(self.accounts)
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="shopee-check") as executor:
            pending = {}
            while True:
                while not self.isInterruptionRequested() and len(pending) < 3:
                    account = next(accounts, None)
                    if account is None:
                        break
                    pending[executor.submit(self._check, account)] = account
                if not pending:
                    break
                completed, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in completed:
                    account = pending.pop(future)
                    result = future.result()
                    if not self.isInterruptionRequested():
                        self.checked.emit(account.id, account.fingerprint(), result)


class AccountEditor(QDialog):
    def __init__(self, account: Account | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sửa tài khoản Shopee" if account else "Thêm cookie Shopee")
        self.resize(660, 330)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.label = QLineEdit(account.label if account else "")
        self.cookie = QPlainTextEdit(account.cookie if account else "")
        self.cookie.setPlaceholderText("SPC_ST=…; … hoặc chỉ giá trị SPC_ST")
        self.proxy = QLineEdit(account.proxy if account else "")
        self.proxy.setPlaceholderText("host:port:user:pass hoặc http://… / socks5://…")
        form.addRow("Tên / ghi chú", self.label)
        form.addRow("Cookie", self.cookie)
        form.addRow("Proxy", self.proxy)
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        try:
            self.value = (self.label.text().strip(), normalize_cookie(self.cookie.toPlainText()),
                          normalize_proxy(self.proxy.text()) if self.proxy.text().strip() else "")
        except ShopeeError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class CopyTable(QTableWidget):
    def __init__(self, rows, columns):
        super().__init__(rows, columns)
        shortcut = QShortcut(QKeySequence.Copy, self)
        shortcut.setContext(Qt.WidgetShortcut)
        shortcut.activated.connect(self._copy)

    def _copy(self):
        cells = self.selectedIndexes()
        rows = sorted({i.row() for i in cells})
        columns = sorted({i.column() for i in cells})
        if rows and columns:
            QApplication.clipboard().setText("\n".join("\t".join(
                self.item(row, col).text() if self.item(row, col) else "" for col in columns)
                for row in rows))


class ProxyAssignmentDialog(QDialog):
    def __init__(self, total: int, selected: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gán proxy")
        self.resize(720, 490)
        self.counts = {"all": total, "selected": selected}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItem(f"Toàn bộ {total} cookie, từ trên xuống dưới", "all")
        if selected:
            self.scope.addItem(f"{selected} cookie đã chọn, từ trên xuống dưới", "selected")
        self.mode = QComboBox()
        self.mode.addItem("Gán lần lượt; cookie dư để trống proxy", "sequential")
        self.mode.addItem("Gán lần lượt và lặp lại danh sách proxy", "cycle")
        self.mode.addItem("Dùng một proxy cho tất cả cookie", "shared")
        form.addRow("Áp dụng cho", self.scope)
        form.addRow("Cách gán", self.mode)
        layout.addLayout(form)
        hint = QLabel("Mỗi dòng một proxy: host:port, host:port:user:pass hoặc URL HTTP/HTTPS/SOCKS5. "
                      "Proxy trong phạm vi đã chọn sẽ được thay bằng kết quả bên dưới.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("172.30.0.91:3128")
        layout.addWidget(self.editor)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.Apply)
        self.apply_button.setText("Gán proxy")
        self.apply_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.scope.currentIndexChanged.connect(self._preview)
        self.mode.currentIndexChanged.connect(self._preview)
        self.editor.textChanged.connect(self._preview)
        self._preview()

    def _preview(self):
        self.assignments = []
        try:
            count = self.counts[self.scope.currentData()]
            lines = self.editor.toPlainText().splitlines()
            self.assignments = proxy_assignment(lines, count, self.mode.currentData())
            used = sum(bool(value) for value in self.assignments)
            supplied = sum(bool(line.strip()) for line in lines)
            message = f"{count} cookie: gán proxy cho {used}, để trống proxy {count - used}."
            if self.mode.currentData() == "cycle":
                message += f" {max(0, count - supplied)} cookie dùng lại proxy từ đầu danh sách."
            elif supplied > count:
                message += f" {supplied - count} proxy cuối không được dùng."
            self.preview.setText(message)
            self.apply_button.setEnabled(True)
        except ShopeeError as exc:
            self.preview.setText(str(exc))
            self.apply_button.setEnabled(False)


class VoucherPopup(QFrame):
    """Scrollable voucher list anchored to one account's Voucher cell."""
    def __init__(self, parent):
        super().__init__(parent, Qt.Popup)
        self.setWindowTitle("Danh sách voucher")
        self.setFrameShape(QFrame.StyledPanel)
        self.account_id = ""
        self.fingerprint = ""
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.table = ShopeeDialog._table(["Mã voucher", "Tên", "Shop", "Giảm", "Tối đa", "Đơn tối thiểu", "Hết hạn"])
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 120)
        for column in (3, 4, 5):
            self.table.setColumnWidth(column, 100)
        layout.addWidget(self.table)
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        copy_button = QPushButton("Copy đã chọn")
        copy_button.clicked.connect(self.table._copy)
        buttons.addWidget(copy_button)
        buttons.addStretch()
        close_button = QPushButton("Đóng danh sách")
        close_button.clicked.connect(self.hide)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def load_account(self, account: Account):
        self.account_id, self.fingerprint = account.id, account.fingerprint()
        result = account.result
        vouchers = result.get("vouchers", [])
        identity = result.get("username") or account.label
        self.title.setText(f"Voucher của {identity} — {len(vouchers)} voucher")
        self.table.setRowCount(len(vouchers))
        for index, row in enumerate(vouchers):
            for column, key in enumerate(("code", "title", "shop", "discount", "cap", "min_spend", "expires")):
                value = str(row.get(key, ""))
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        if result.get("voucher_error"):
            message = "Voucher: " + result["voucher_error"]
        elif "vouchers" not in result:
            message = "Chưa có dữ liệu voucher. Bấm Check đã chọn hoặc Check tất cả để lấy danh sách."
        elif not vouchers:
            message = "Không có voucher hiện có trong API trả về."
        else:
            message = "Cuộn để xem danh sách. Chọn voucher rồi Ctrl+C hoặc Copy đã chọn."
        self.message.setText(message)

    def open_at(self, account: Account, anchor: QPoint, above: int):
        self.load_account(account)
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        height = min(480, max(230, 160 + 30 * min(10, self.table.rowCount())))
        self.resize(min(1080, bounds.width()), min(height, bounds.height()))
        x = max(bounds.left(), min(anchor.x(), bounds.right() - self.width() + 1))
        y = anchor.y()
        if y + self.height() > bounds.bottom() + 1:
            y = above - self.height()
        y = max(bounds.top(), min(y, bounds.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        self.table.setFocus()


class ShopeeDialog(QDialog):
    VOUCHER_COLUMN = 6

    def __init__(self, store: AccountStore, parent=None):
        # MainWindow retains this window and shuts it down explicitly. An
        # unowned normal window gets its own taskbar entry on Windows.
        super().__init__(None, Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle("Shopee — Cookie, đơn hàng và voucher")
        self.resize(1160, 760)
        self.store = store
        self.worker: CheckWorker | None = None
        self.voucher_popup: VoucherPopup | None = None
        self._buttons = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Danh sách tự lưu trong ControlIOS PC. Mỗi tài khoản dùng proxy của dòng đó khi kiểm tra."))
        buttons = QHBoxLayout()
        for text, slot in [("Thêm cookie", self._add), ("Dán danh sách", self._import),
                           ("Sửa", self._edit), ("Xóa", self._remove),
                           ("Gán proxy", self._assign_proxies), ("Copy cookie", self._copy_cookie),
                           ("Check đã chọn", self._check_selected), ("Check tất cả", self._check_all)]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            self._buttons.append(button)
            buttons.addWidget(button)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        splitter = QSplitter(Qt.Vertical)
        self.table = self._table(["Tên / thiết bị", "Username", "Cookie", "Proxy", "Kết quả", "Đơn", "Voucher", "Kiểm tra lúc"])
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 220)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._details)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.cellActivated.connect(self._cell_clicked)
        self.table.itemDoubleClicked.connect(self._double_clicked)
        splitter.addWidget(self.table)
        self.tabs = QTabWidget()
        self.orders = self._table(["Mã đơn / ID", "Mã vận đơn", "Trạng thái", "Người nhận", "Điện thoại", "Địa chỉ", "Sản phẩm", "Link"])
        self.vouchers = self._table(["Mã voucher", "Tên", "Shop", "Giảm", "Tối đa", "Đơn tối thiểu", "Hết hạn"])
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.tabs.addTab(self.orders, "Đơn gần đây")
        self.tabs.addTab(self.vouchers, "Voucher hiện có")
        self.tabs.addTab(self.notes, "Kết quả / cảnh báo")
        splitter.addWidget(self.tabs)
        splitter.setSizes([350, 300])
        layout.addWidget(splitter)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._refresh()

    @staticmethod
    def _table(headers):
        table = CopyTable(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setDefaultSectionSize(30)
        table.setWordWrap(False)
        table.setColumnWidth(0, 190)
        table.setColumnWidth(2, 220)
        return table

    def _selected(self) -> list[Account]:
        return [self.store.accounts[index.row()] for index in self.table.selectionModel().selectedRows()]

    def _refresh(self):
        selected = {a.id for a in self._selected()}
        current = self.table.currentRow()
        current_id = self.store.accounts[current].id if 0 <= current < len(self.store.accounts) else ""
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.store.accounts))
        for index, account in enumerate(self.store.accounts):
            result = account.result
            token = account.fingerprint()[:10]
            values = [account.label, result.get("username") or "—", "SPC_ST • " + token, proxy_label(account.proxy),
                      result.get("status", "Chưa kiểm tra"),
                      self._count(result, "orders", "order_error"),
                      self._count(result, "vouchers", "voucher_error") + " ▾", account.checked_at]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column == self.VOUCHER_COLUMN:
                    item.setToolTip("Bấm để xổ danh sách voucher của cookie này.")
                self.table.setItem(index, column, item)
            if account.id == current_id:
                self.table.setCurrentCell(index, 0)
        self.table.clearSelection()
        if not selected and self.store.accounts:
            selected = {self.store.accounts[0].id}
        for index, account in enumerate(self.store.accounts):
            if account.id in selected:
                self.table.selectionModel().select(self.table.model().index(index, 0),
                                                   QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table.blockSignals(False)
        if self.voucher_popup is not None and self.voucher_popup.isVisible():
            account = next((a for a in self.store.accounts if a.id == self.voucher_popup.account_id), None)
            if account is None or account.fingerprint() != self.voucher_popup.fingerprint:
                self.voucher_popup.hide()
            else:
                self.voucher_popup.load_account(account)
        self._details()

    def _cell_clicked(self, row, column):
        if column != self.VOUCHER_COLUMN or not 0 <= row < len(self.store.accounts):
            return
        if self.voucher_popup is None:
            self.voucher_popup = VoucherPopup(self)
        rect = self.table.visualItemRect(self.table.item(row, column))
        viewport = self.table.viewport()
        self.voucher_popup.open_at(self.store.accounts[row], viewport.mapToGlobal(rect.bottomLeft()),
                                   viewport.mapToGlobal(rect.topLeft()).y())
        self.tabs.setCurrentIndex(1)

    def _double_clicked(self, item):
        if item.column() != self.VOUCHER_COLUMN:
            self._edit()

    @staticmethod
    def _count(result, key, error):
        if not result:
            return "—"
        count = len(result.get(key, []))
        return f"{count} (có cảnh báo)" if result.get(error) else str(count)

    def _save(self):
        try:
            self.store.save()
            return True
        except OSError:
            self.status.setText("Không lưu được danh sách Shopee. Kiểm tra quyền ghi/thư mục dữ liệu.")
            return False

    def capture(self, source: str, label: str, cookie: str):
        try:
            self.store.upsert(cookie, label=label, source=source)
        except ShopeeError:
            return
        self._save()
        self._refresh()

    def _add(self):
        if self._busy():
            return
        editor = AccountEditor(parent=self)
        if editor.exec() == QDialog.Accepted:
            label, cookie, proxy = editor.value
            self.store.upsert(cookie, label, proxy)
            self._save()
            self._refresh()

    def _edit(self):
        if self._busy():
            return
        selected = self._selected()
        if len(selected) != 1:
            self.status.setText("Chọn một tài khoản để sửa.")
            return
        account = selected[0]
        editor = AccountEditor(account, self)
        if editor.exec() == QDialog.Accepted:
            label, cookie, proxy = editor.value
            if cookie != account.cookie or proxy != account.proxy:
                account.result = {}
                account.checked_at = ""
            account.label, account.cookie, account.proxy = label or account.label, cookie, proxy
            self._save()
            self._refresh()

    def _multiline(self, title, hint):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(700, 400)
        layout = QVBoxLayout(dialog)
        label = QLabel(hint)
        label.setWordWrap(True)
        layout.addWidget(label)
        editor = QPlainTextEdit()
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            return [line.strip(" \r") for line in editor.toPlainText().splitlines() if line.strip()]
        return None

    def _import(self):
        if self._busy():
            return
        lines = self._multiline("Dán danh sách cookie", "Mỗi dòng một cookie hoặc SPC_ST. Có thể dán 3 cột phân cách bằng TAB: Tên, Cookie, Proxy.")
        if lines is None:
            return
        prepared, invalid = [], []
        for index, line in enumerate(lines, 1):
            try:
                parts = line.split("\t")
                if len(parts) == 1:
                    prepared.append(("", normalize_cookie(parts[0]), ""))
                elif len(parts) == 3:
                    prepared.append((parts[0], normalize_cookie(parts[1]), normalize_proxy(parts[2]) if parts[2] else ""))
                else:
                    raise ShopeeError("Cần một cookie hoặc đủ 3 cột TAB.")
            except ShopeeError as exc:
                invalid.append(f"Dòng {index}: {exc}")
        if invalid:
            QMessageBox.warning(self, "Chưa nhập danh sách", "\n".join(invalid[:10]))
            return
        for label, cookie, proxy in prepared:
            self.store.upsert(cookie, label, proxy)
        self._save()
        self._refresh()
        self.status.setText(f"Đã nhập/cập nhật {len(prepared)} cookie.")

    def _assign_proxies(self):
        if self._busy():
            return
        if not self.store.accounts:
            self.status.setText("Thêm cookie trước khi gán proxy.")
            return
        selected_ids = {account.id for account in self._selected()}
        dialog = ProxyAssignmentDialog(len(self.store.accounts), len(selected_ids), self)
        if dialog.exec() != QDialog.Accepted:
            return
        accounts = [account for account in self.store.accounts
                    if dialog.scope.currentData() == "all" or account.id in selected_ids]
        changed = 0
        for account, proxy in zip(accounts, dialog.assignments):
            if account.proxy != proxy:
                account.proxy, account.result, account.checked_at = proxy, {}, ""
                changed += 1
        saved = self._save()
        self._refresh()
        if saved:
            self.status.setText(f"Đã gán proxy cho {len(accounts)} cookie; thay đổi {changed} dòng.")

    def _remove(self):
        if self._busy():
            return
        accounts = self._selected()
        if not accounts:
            return
        if QMessageBox.question(self, "Xóa cookie", f"Xóa {len(accounts)} tài khoản khỏi bảng Shopee?") != QMessageBox.Yes:
            return
        self.table.clearSelection()
        self.table.setRowCount(0)
        self.store.accounts = [a for a in self.store.accounts if a not in accounts]
        self._save()
        self._refresh()

    def _copy_cookie(self):
        selected = self._selected()
        if selected:
            QApplication.clipboard().setText("\n".join(a.cookie for a in selected))
            self.status.setText(f"Đã copy {len(selected)} cookie.")

    def _details(self):
        selected = self._selected()
        result = selected[0].result if selected else {}
        orders, vouchers = result.get("orders", []), result.get("vouchers", [])
        self.orders.setRowCount(len(orders))
        for index, row in enumerate(orders):
            values = [row.get("order_sn") or row.get("order_id"), *[row.get(k, "") for k in
                      ("tracking", "status", "receiver", "phone", "address", "products", "links")]]
            for column, value in enumerate(values):
                self.orders.setItem(index, column, QTableWidgetItem(str(value)))
        self.vouchers.setRowCount(len(vouchers))
        for index, row in enumerate(vouchers):
            for column, key in enumerate(("code", "title", "shop", "discount", "cap", "min_spend", "expires")):
                self.vouchers.setItem(index, column, QTableWidgetItem(str(row.get(key, ""))))
        messages = [result.get("status", "Chưa kiểm tra")]
        if result.get("username"):
            messages.append("Username: " + result["username"])
        messages += [("Username: " if k == "profile_error" else "") + result[k]
                     for k in ("profile_error", "order_error", "voucher_error") if result.get(k)]
        if result and not result.get("order_error") and not orders:
            messages.append("Không có đơn trong danh sách API trả về.")
        if result and not result.get("voucher_error") and not vouchers:
            messages.append("Không có voucher hiện có trong API trả về.")
        self.notes.setPlainText("\n\n".join(messages))
        self.tabs.setTabText(0, f"Đơn gần đây ({len(orders)})")
        self.tabs.setTabText(1, f"Voucher hiện có ({len(vouchers)})")

    def _busy(self):
        return self.worker is not None and self.worker.isRunning()

    def _check_selected(self):
        self.start_checks(self._selected())

    def _check_all(self):
        self.start_checks(self.store.accounts)

    def start_checks(self, accounts):
        if self.worker is not None:
            return
        if not accounts:
            self.status.setText("Chọn tài khoản hoặc thêm cookie trước.")
            return
        self._completed = 0
        self._total = len(accounts)
        self.worker = CheckWorker(accounts, self)
        self.worker.checked.connect(self._checked)
        self.worker.finished.connect(self._finished)
        for button in self._buttons:
            button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText(f"Đang kiểm tra 0/{self._total} tài khoản…")
        self.worker.start()

    def _checked(self, account_id, fingerprint, result):
        account = next((a for a in self.store.accounts if a.id == account_id), None)
        if account and account.fingerprint() == fingerprint:
            account.result = result
            account.checked_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            self._save()
            self._refresh()
        self._completed += 1
        self.status.setText(f"Đã kiểm tra {self._completed}/{self._total} tài khoản.")

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        for button in self._buttons:
            button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status.setText(f"Kết thúc: đã kiểm tra {self._completed}/{self._total} tài khoản.")

    def _stop(self):
        if self.worker:
            self.worker.requestInterruption()
            self.status.setText("Đang dừng; chờ yêu cầu proxy hiện tại kết thúc…")

    def shutdown(self) -> bool:
        self._stop()
        if self.worker is None:
            self.hide()
        return self.worker is None

    def hideEvent(self, event):
        if self.voucher_popup is not None:
            self.voucher_popup.hide()
        super().hideEvent(event)

    def reject(self):
        # Closing the table hides it; a running worker remains owned until done.
        self.hide()
