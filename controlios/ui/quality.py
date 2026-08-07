"""Chỉnh chất lượng và tốc độ khung hình.

Thay đổi áp dụng **ngay lập tức**: các phiên đọc `Settings` ở mỗi vòng nhịp,
nên không phải nối lại hay khởi động lại phần mềm.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

# Mức scale khung hình máy gửi về (nhãn, hệ số).
SCALE_LEVELS = [
    ("Gốc (nét nhất)", 1.0),
    ("0.85×", 0.85),
    ("0.75×", 0.75),
    ("0.5× (nhẹ nhất)", 0.5),
    ("0.35×", 0.35),
]

from ..config import Settings

# (nhãn, live_fps, live_long_edge, grid_fps, thumb_long_edge)
PRESETS = [
    ("Mượt — nhẹ máy nhất", 8.0, 640, 0.5, 240),
    ("Cân bằng", 12.0, 900, 1.0, 320),
    ("Nét — nặng nhất", 20.0, 0, 2.0, 420),
]


class QualityDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Chất lượng và tốc độ khung hình")
        self.resize(460, 430)

        layout = QVBoxLayout(self)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Mẫu sẵn:"))
        for label, live_fps, live_edge, grid_fps, thumb_edge in PRESETS:
            button = QPushButton(label.split(" —")[0])
            button.setToolTip(label)
            button.clicked.connect(
                lambda _c=False, a=live_fps, b=live_edge, c=grid_fps, d=thumb_edge:
                self._apply_preset(a, b, c, d)
            )
            preset_row.addWidget(button)
        layout.addLayout(preset_row)

        live_box = QGroupBox("Máy đang mở (khung điều khiển)")
        live_form = QFormLayout(live_box)

        self.live_fps = QDoubleSpinBox()
        self.live_fps.setRange(1.0, 60.0)
        self.live_fps.setSingleStep(1.0)
        self.live_fps.setSuffix(" hình/giây")
        self.live_fps.setToolTip("Cao thì mượt hơn nhưng nặng cả PC lẫn iPhone")
        live_form.addRow("Tốc độ:", self.live_fps)

        self.live_edge = QSpinBox()
        self.live_edge.setRange(240, 2000)
        self.live_edge.setSingleStep(20)
        self.live_edge.setSuffix(" px")
        self.live_edge.setToolTip(
            "Cạnh dài của khung hình nhận về. Khung điều khiển chỉ rộng chừng "
            "500 px nên nhận lớn hơn là sao chép thừa."
        )
        live_form.addRow("Độ nét:", self.live_edge)

        self.live_full = QCheckBox("Giữ nguyên độ phân giải gốc (nét nhất, nặng nhất)")
        self.live_full.toggled.connect(self.live_edge.setDisabled)
        live_form.addRow("", self.live_full)
        layout.addWidget(live_box)

        grid_box = QGroupBox("Ô trong lưới")
        grid_form = QFormLayout(grid_box)

        self.grid_fps = QDoubleSpinBox()
        self.grid_fps.setRange(0.1, 10.0)
        self.grid_fps.setSingleStep(0.1)
        self.grid_fps.setSuffix(" hình/giây")
        self.grid_fps.setToolTip("Nhân với số ô đang nhìn thấy, nên đừng đặt cao")
        grid_form.addRow("Tốc độ:", self.grid_fps)

        self.thumb_edge = QSpinBox()
        self.thumb_edge.setRange(120, 800)
        self.thumb_edge.setSingleStep(20)
        self.thumb_edge.setSuffix(" px")
        grid_form.addRow("Độ nét:", self.thumb_edge)
        layout.addWidget(grid_box)

        idle_box = QGroupBox("Giảm tải cho iPhone")
        idle_form = QFormLayout(idle_box)
        self.idle_after = QSpinBox()
        self.idle_after.setRange(0, 3600)
        self.idle_after.setSuffix(" giây")
        self.idle_after.setSpecialValueText("không bao giờ ngắt")
        self.idle_after.setToolTip(
            "Máy ngoài khung nhìn bấy lâu thì ngắt hẳn, để TrollVNC trên máy "
            "dừng chụp hình. Cuộn tới thì tự nối lại."
        )
        idle_form.addRow("Ngắt máy không xem sau:", self.idle_after)

        self.scale_combo = QComboBox()
        for label, value in SCALE_LEVELS:
            self.scale_combo.addItem(label, value)
        self.scale_combo.setToolTip(
            "Máy gửi khung nhỏ hơn -> nén nhẹ hơn -> MƯỢT hơn trên máy đời cũ "
            "(iPhone 6s...), đổi lại kém nét. Đây là cách giảm tải THẬT (trên "
            "máy), khác với Độ nét ở trên (chỉ thu nhỏ ở PC). Cần TrollVNC đã vá."
        )
        idle_form.addRow("Scale khung máy gửi:", self.scale_combo)
        layout.addWidget(idle_box)

        smooth_box = QGroupBox("Độ mượt (áp thẳng lên máy — không nối lại)")
        smooth_form = QFormLayout(smooth_box)
        self.low_latency = QCheckBox("Ưu tiên độ trễ THẤP (bỏ khung cũ, Q=1)")
        self.low_latency.setToolTip(
            "Bật: máy bỏ khung cũ khi chưa gửi kịp -> điều khiển bám tay hơn "
            "(hợp thao tác auto/bấm nhanh). Tắt: giữ tối đa 2 khung -> mượt hơn "
            "khi mạng ổn nhưng trễ hơn."
        )
        smooth_form.addRow("", self.low_latency)
        self.orient_sync = QCheckBox("Đồng bộ xoay màn hình theo máy")
        self.orient_sync.setToolTip(
            "TẮT (khuyên dùng cho farm): bỏ qua xoay -> KHÔNG resize khi app xoay "
            "-> hết cảnh chớp đen/nối lại giữa chừng. Bật nếu thật sự cần xem xoay."
        )
        smooth_form.addRow("", self.orient_sync)
        layout.addWidget(smooth_box)

        self.natural_scroll = QCheckBox("Cuộn thuận iOS (lăn bánh xe khớp chiều vuốt iPhone)")
        self.natural_scroll.setToolTip(
            "Bật: lăn bánh xe lên làm nội dung dịch như vuốt lên trên iPhone. "
            "Tắt: giữ chiều kiểu desktop (ngược với iOS)."
        )
        layout.addWidget(self.natural_scroll)

        note = QLabel(
            "Tốc độ/độ nét có hiệu lực ngay, không phải nối lại máy.\n"
            "Đổi Scale khung máy gửi sẽ làm mỗi máy nối lại một nhịp (như xoay "
            "máy) và cần TrollVNC đã vá."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa4b2;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Áp dụng")
        buttons.button(QDialogButtonBox.Cancel).setText("Huỷ")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load()

    # ------------------------------------------------------------------ dữ liệu

    def load(self) -> None:
        self.live_fps.setValue(self.settings.live_fps)
        self.grid_fps.setValue(self.settings.grid_fps)
        self.thumb_edge.setValue(self.settings.thumb_long_edge)
        full = self.settings.live_long_edge == 0
        self.live_full.setChecked(full)
        self.live_edge.setValue(self.settings.live_long_edge or 900)
        self.live_edge.setDisabled(full)
        self.idle_after.setValue(int(self.settings.idle_disconnect_after))
        self._select_scale(self.settings.device_scale)
        self.low_latency.setChecked(getattr(self.settings, "device_low_latency", True))
        self.orient_sync.setChecked(getattr(self.settings, "device_orientation_sync", False))
        self.natural_scroll.setChecked(getattr(self.settings, "natural_scroll", True))

    def _select_scale(self, value: float) -> None:
        # Chọn mức gần nhất với giá trị hiện có.
        best = min(range(self.scale_combo.count()),
                   key=lambda i: abs(self.scale_combo.itemData(i) - value))
        self.scale_combo.setCurrentIndex(best)

    def apply(self) -> None:
        self.settings.live_fps = self.live_fps.value()
        self.settings.grid_fps = self.grid_fps.value()
        self.settings.thumb_long_edge = self.thumb_edge.value()
        self.settings.live_long_edge = 0 if self.live_full.isChecked() \
            else self.live_edge.value()
        self.settings.idle_disconnect_after = float(self.idle_after.value())
        self.settings.device_scale = float(self.scale_combo.currentData())
        self.settings.device_low_latency = self.low_latency.isChecked()
        self.settings.device_orientation_sync = self.orient_sync.isChecked()
        self.settings.natural_scroll = self.natural_scroll.isChecked()

    def scale_value(self) -> float:
        return float(self.scale_combo.currentData())

    def _apply_preset(self, live_fps: float, live_edge: int,
                      grid_fps: float, thumb_edge: int) -> None:
        self.live_fps.setValue(live_fps)
        self.grid_fps.setValue(grid_fps)
        self.thumb_edge.setValue(thumb_edge)
        self.live_full.setChecked(live_edge == 0)
        if live_edge:
            self.live_edge.setValue(live_edge)
