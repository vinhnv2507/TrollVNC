"""Bọc khung hình từ luồng mạng thành QImage, không sao chép thêm lần nào."""

from __future__ import annotations

from PySide6.QtGui import QImage

from ..vnc.session import PIXEL_BGRA32, PIXEL_RGB888, PIXEL_RGBX32, Frame

# Cách sắp xếp byte -> định dạng QImage đọc thẳng được, khỏi phải đổi kênh.
QT_FORMATS = {
    PIXEL_RGB888: QImage.Format_RGB888,
    PIXEL_BGRA32: QImage.Format_RGB32,      # little-endian: byte là B,G,R,A
    PIXEL_RGBX32: QImage.Format_RGBX8888,
}


def qimage_for(frame: Frame) -> QImage:
    fmt = QT_FORMATS.get(frame.pixel_format, QImage.Format_RGB888)
    return QImage(frame.data, frame.width, frame.height, frame.bytes_per_line, fmt)
