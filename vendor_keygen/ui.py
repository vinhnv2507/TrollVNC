from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, QSettings, QThread, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QComboBox, QDateTimeEdit, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from . import __version__
from .core import KeygenError, issue_keys, load_manager, load_signer, read_udid


class UdidWorker(QThread):
    result = Signal(str, str)
    def __init__(self, host, port, token, parent=None):
        super().__init__(parent)
        self.host, self.port, self.token = host, port, token
    def run(self):
        try:
            self.result.emit(read_udid(self.host,self.port,self.token), '')
        except KeygenError as error:
            self.result.emit('', str(error))
        except Exception:
            self.result.emit('', 'Không đọc được UDID từ thiết bị.')
        finally:
            self.token = ''


class KeygenWindow(QMainWindow):
    def __init__(self, settings=None, autodetect=True):
        super().__init__()
        self.settings = settings or QSettings('CTLIOS', 'VendorKeygen')
        self.worker = None
        self.results = []
        self.setWindowTitle(f'CTLIOS Keygen {__version__}')
        self.resize(1120, 850)
        container=QWidget();self.setCentralWidget(container)
        layout=QVBoxLayout(container);layout.setSpacing(12)
        title=QLabel('Tạo key bản quyền CTLIOS');title.setStyleSheet('font-size:24px;font-weight:600;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Tạo key theo UDID từng iPhone, dùng để kích hoạt trong Manager CTLIOS.'))
        signer_box=QGroupBox('1. Khoá ký và cấu hình kết nối');form=QFormLayout(signer_box)
        key_row=QHBoxLayout();self.signer_path=QLineEdit();self.signer_path.setPlaceholderText('Chọn controlios_private.pem hiện tại…')
        key_row.addWidget(self.signer_path,1);choose=QPushButton('Chọn khoá ký…');choose.clicked.connect(self._choose_signer);key_row.addWidget(choose)
        form.addRow('Khoá ký riêng',key_row)
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.Password);self.password.setPlaceholderText('Để trống nếu PEM không có mật khẩu')
        form.addRow('Mật khẩu PEM',self.password)
        token_row=QHBoxLayout();self.token=QLineEdit();self.token.setEchoMode(QLineEdit.Password);self.token.setPlaceholderText('Token kết nối đang dùng trong Manager')
        token_row.addWidget(self.token,1);config=QPushButton('Đọc cấu hình Manager…');config.clicked.connect(self._choose_config);token_row.addWidget(config)
        form.addRow('Token kết nối',token_row)
        self.config_label=QLabel('Chưa đọc cấu hình Manager.');self.config_label.setWordWrap(True);form.addRow(self.config_label)
        layout.addWidget(signer_box)
        inputs=QHBoxLayout()
        device_box=QGroupBox('2. Thiết bị');device_layout=QVBoxLayout(device_box)
        lan=QHBoxLayout();self.device_combo=QComboBox();self.device_combo.addItem('— Chọn thiết bị từ Manager —',None);self.device_combo.currentIndexChanged.connect(self._select_device)
        lan.addWidget(self.device_combo,1);self.host=QLineEdit();self.host.setPlaceholderText('IP iPhone');self.host.setMaximumWidth(150);lan.addWidget(self.host)
        self.port=QSpinBox();self.port.setRange(1,65535);self.port.setValue(46752);self.port.setFixedWidth(110);lan.addWidget(self.port)
        self.read_button=QPushButton('Lấy UDID qua LAN');self.read_button.clicked.connect(self._read_udid);lan.addWidget(self.read_button)
        device_layout.addLayout(lan)
        device_layout.addWidget(QLabel('Mỗi dòng: UDID hoặc Tên máy | UDID. Có thể dán nhiều dòng.'))
        self.devices=QPlainTextEdit();self.devices.setPlaceholderText('6s1 | UDID lấy từ Manager\n6s2 | UDID lấy từ Manager');device_layout.addWidget(self.devices)
        inputs.addWidget(device_box,3)
        duration_box=QGroupBox('3. Thời hạn');duration=QVBoxLayout(duration_box)
        self.mode=QComboBox()
        for label,value in [('7 ngày',7),('30 ngày',30),('90 ngày',90),('Số ngày tuỳ chọn','days'),('Ngày hết hạn cụ thể','date'),('Vĩnh viễn',0)]:self.mode.addItem(label,value)
        self.mode.setCurrentIndex(1);self.mode.currentIndexChanged.connect(self._duration_changed);duration.addWidget(self.mode)
        self.days=QSpinBox();self.days.setRange(1,36500);self.days.setValue(30);self.days.setSuffix(' ngày');duration.addWidget(self.days)
        self.date=QDateTimeEdit(QDateTime.currentDateTime().addDays(30));self.date.setCalendarPopup(True);self.date.setDisplayFormat('dd/MM/yyyy HH:mm:ss');duration.addWidget(self.date)
        self.expiry_label=QLabel();self.expiry_label.setWordWrap(True);duration.addWidget(self.expiry_label)
        help_label=QLabel('Hạn tính từ lúc tạo key, không phải lúc nhập key. Ngày giờ theo máy PC.');help_label.setWordWrap(True);duration.addWidget(help_label);duration.addStretch()
        self.generate_button=QPushButton('Tạo key');self.generate_button.setMinimumHeight(42);self.generate_button.clicked.connect(self.generate);duration.addWidget(self.generate_button)
        inputs.addWidget(duration_box,1);layout.addLayout(inputs,1)
        self.days.valueChanged.connect(self._update_expiry);self.date.dateTimeChanged.connect(self._update_expiry);self._duration_changed()
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['Tên máy','UDID','Hết hạn','Key kích hoạt'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows);self.table.setSelectionMode(QTableWidget.SingleSelection);self.table.setEditTriggers(QTableWidget.NoEditTriggers);self.table.setWordWrap(False)
        self.table.setColumnWidth(0,110);self.table.setColumnWidth(1,290);self.table.setColumnWidth(2,170);self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(150);layout.addWidget(self.table,1)
        actions=QHBoxLayout()
        self.copy_button=QPushButton('Copy key dòng chọn');self.copy_button.clicked.connect(self.copy_selected)
        self.copy_all_button=QPushButton('Copy tất cả (UDID + key)');self.copy_all_button.clicked.connect(self.copy_all)
        self.export_button=QPushButton('Lưu danh sách…');self.export_button.clicked.connect(self.export)
        self.clear_button=QPushButton('Xoá kết quả');self.clear_button.clicked.connect(self.clear_results)
        for button in [self.copy_button,self.copy_all_button,self.export_button,self.clear_button]:actions.addWidget(button)
        layout.addLayout(actions)
        self.status=QLabel('Khoá ký riêng không được nhúng vào EXE. Kết quả chỉ lưu khi bạn bấm Lưu danh sách.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.setStyleSheet('QGroupBox {font-weight:600; padding-top:10px;} QLineEdit,QSpinBox,QComboBox {min-height:26px;} QPushButton {padding:7px 12px;} QPlainTextEdit,QTableWidget {font-family:Consolas; font-size:12px;}')
        self._result_buttons()
        if autodetect:self._autodetect()

    def _autodetect(self):
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent
        candidates=[Path(str(self.settings.value('signer_path','')))] if self.settings.value('signer_path') else []
        candidates.extend([base/'controlios_private.pem',*(p/'tools/controlios_private.pem' for p in [base,*base.parents])])
        found=next((p for p in candidates if p.is_file()),None)
        if found:self.signer_path.setText(str(found))
        appdata=Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))
        configs=[Path(str(self.settings.value('registry_path')))] if self.settings.value('registry_path') else []
        configs.extend([appdata/'Manager CTLIOS/config/devices.json',appdata/'ControlIOS PC/config/devices.json',base/'config/devices.json'])
        for config in configs:
            if config.is_file():
                try:self._load_config(config);break
                except KeygenError:continue

    def _choose_signer(self):
        path,_=QFileDialog.getOpenFileName(self,'Chọn khoá ký CTLIOS',self.signer_path.text(),'Khoá PEM (*.pem);;Tất cả (*)')
        if path:self.signer_path.setText(path);self.settings.setValue('signer_path',path)

    def _choose_config(self):
        path,_=QFileDialog.getOpenFileName(self,'Chọn config/devices.json của Manager','','Cấu hình JSON (*.json)')
        if path:
            try:self._load_config(Path(path));self.status.setText('Đã đọc token và danh sách thiết bị từ Manager.')
            except KeygenError as error:self.status.setText(str(error))

    def _load_config(self,path):
        token,devices=load_manager(path)
        self.token.setText(token);self.device_combo.clear();self.device_combo.addItem('— Chọn thiết bị từ Manager —',None)
        for device in devices:
            self.device_combo.addItem(f"{device.get('name') or device.get('host','')} · {device.get('host','')}",device)
        self.config_label.setText(f'Cấu hình: {path} · {len(devices)} máy');self.settings.setValue('registry_path',str(path))

    def _select_device(self):
        device=self.device_combo.currentData()
        if device:self.host.setText(device.get('host',''));self.port.setValue(device.get('control_port') or 46752)

    def _read_udid(self):
        if self.worker and self.worker.isRunning():return
        self._requested_name=str((self.device_combo.currentData() or {}).get('name',''))
        for widget in [self.device_combo,self.host,self.port]:widget.setEnabled(False)
        self.read_button.setEnabled(False);self.status.setText('Đang đọc UDID từ iPhone…')
        self.worker=UdidWorker(self.host.text(),self.port.value(),self.token.text(),self)
        self.worker.result.connect(self._udid_result);self.worker.finished.connect(self._read_finished);self.worker.start()

    def _read_finished(self):
        self.read_button.setEnabled(True)
        for widget in [self.device_combo,self.host,self.port]:widget.setEnabled(True)
        self._requested_name=None
        if not self.isVisible() and getattr(self,'_closing',False):self.close()

    def _udid_result(self,udid,error):
        if error:self.status.setText(error);return
        # Preserve the exact UDID casing returned by the daemon.
        if udid.lower() not in self.devices.toPlainText().lower():
            device=self.device_combo.currentData() or {}
            name=getattr(self,'_requested_name',None)
            if name is None:name=str(device.get('name',''))
            name=name.replace('|',' ').replace('\n',' ')
            self.devices.appendPlainText(f'{name} | {udid}' if name else udid)
        self.status.setText('Đã thêm UDID vào danh sách. Chọn thời hạn rồi bấm Tạo key.')

    def expiry(self):
        mode=self.mode.currentData()
        if mode==0:return 0
        if mode=='date':return self.date.dateTime().toSecsSinceEpoch()
        return int(time.time())+86400*(self.days.value() if mode=='days' else mode)

    def _duration_changed(self):
        self.days.setVisible(self.mode.currentData()=='days');self.date.setVisible(self.mode.currentData()=='date');self._update_expiry()

    def _update_expiry(self):
        expiry=self.expiry();self.expiry_label.setText('Dự kiến hết hạn: '+(datetime.fromtimestamp(expiry).strftime('%d/%m/%Y %H:%M:%S') if expiry else 'Vĩnh viễn'))

    def generate(self):
        try:
            signer=load_signer(Path(self.signer_path.text()),self.password.text())
            results=issue_keys(signer,self.devices.toPlainText(),self.token.text(),self.expiry())
        except KeygenError as error:self.status.setText(str(error));return
        self.results=results;self.table.setRowCount(len(results))
        for row,result in enumerate(results):
            end=datetime.fromtimestamp(result.expiry).strftime('%d/%m/%Y %H:%M:%S') if result.expiry else 'Vĩnh viễn'
            for col,text in enumerate([result.name,result.udid,end,result.license]):self.table.setItem(row,col,QTableWidgetItem(text))
        self.table.selectRow(0);self.settings.setValue('signer_path',self.signer_path.text());self._result_buttons()
        self.status.setText(f'Đã tạo {len(results)} key. Copy key vào dòng thiết bị tương ứng trong Manager → Kích hoạt bản quyền qua LAN.')

    def _result_buttons(self):
        for button in [self.copy_button,self.copy_all_button,self.export_button,self.clear_button]:button.setEnabled(bool(self.results))

    def copy_selected(self):
        row=self.table.currentRow()
        if 0<=row<len(self.results):QApplication.clipboard().setText(self.results[row].license);self.status.setText('Đã copy key của dòng đang chọn.')

    def copy_all(self):
        QApplication.clipboard().setText('\n'.join(r.udid+'\t'+r.license for r in self.results));self.status.setText('Đã copy danh sách UDID và key.')

    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'Lưu key đã tạo','CTLIOS-keys.txt','Văn bản (*.txt)')
        if not path:return
        try:
            Path(path).write_text('\n'.join('\t'.join([r.name,r.udid,datetime.fromtimestamp(r.expiry).strftime('%d/%m/%Y %H:%M:%S') if r.expiry else 'Vĩnh viễn',r.license]) for r in self.results)+'\n',encoding='utf-8')
        except OSError:self.status.setText('Không lưu được file. Chọn thư mục khác.');return
        self.status.setText('Đã lưu danh sách key vào file bạn chọn.')

    def clear_results(self):
        self.results=[];self.table.setRowCount(0);self._result_buttons();self.status.setText('Đã xoá kết quả trên tool.')

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self._closing=True;self.hide();event.ignore();return
        self.password.clear();self.token.clear();self.clear_results();super().closeEvent(event)


def main():
    app=QApplication(sys.argv)
    app.setFont(QFont('Segoe UI',10))
    app.setApplicationName('CTLIOS Keygen');app.setApplicationVersion(__version__)
    window=KeygenWindow();window.show()
    return app.exec()
