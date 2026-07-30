"""Cử chỉ iOS dựng sẵn — `home`, `openapp`, `closeapp`…

VNC chỉ có màn hình và chuột/phím: không có kênh nào hỏi iOS "đang cài app gì"
hay "mở bundle id này". Nên các lệnh app ở đây là **chuỗi cử chỉ**, đúng như
bạn tự thao tác tay.

Vì vậy toạ độ phụ thuộc đời máy và phiên bản iOS. Mỗi macro là một đoạn kịch
bản bằng chữ, và người dùng chỉnh lại được trong `config/gestures.json` mà
không cần sửa code:

    {
      "home": "swipe 0.5 0.99 0.5 0.55 0.18\\nwait 0.6"
    }

Mặc định dưới đây nhắm iPhone **Face ID** (không nút Home), màn hình dọc. Máy
có nút Home vật lý (SE, 8) thì `home` nên đổi thành `key Home` — nếu TrollVNC
bên máy có map keysym đó.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .config import PROJECT_ROOT

GESTURES_PATH = PROJECT_ROOT / "config" / "gestures.json"

# Lệnh nguyên thuỷ của kịch bản — macro không được trùng tên, nếu không người
# dùng sẽ vô tình định nghĩa đè lên `tap` rồi không hiểu vì sao kịch bản sai.
RESERVED = frozenset({"tap", "button", "swipe", "text", "key", "wait", "shot",
                      "repeat", "macro"})

# Mỗi macro là kịch bản con; {name} là tham số truyền vào.
DEFAULT_GESTURES: Dict[str, str] = {
    # TrollVNC map chuột phải thành nút Home, nên đây là một cú bấm nút thật —
    # không phụ thuộc toạ độ, chạy đúng trên mọi đời máy.
    "home": (
        "button home\n"
        "wait 0.7"
    ),
    # Bấm Home hai lần nhanh -> trình chuyển app.
    "switcher": (
        "button home\n"
        "wait 0.12\n"
        "button home\n"
        "wait 1.0"
    ),
    # Chuột giữa là nút Power.
    "lock": (
        "button power\n"
        "wait 0.5"
    ),
    # Phương án dự phòng bằng cử chỉ, cho máy nào bấm nút không ăn.
    "home_swipe": (
        "swipe 0.5 0.99 0.5 0.55 0.18\n"
        "wait 0.7"
    ),
    "switcher_swipe": (
        "swipe 0.5 0.99 0.5 0.45 0.35 0.7\n"
        "wait 1.0"
    ),
    # Trên màn hình chính, vuốt xuống ở giữa -> ô tìm kiếm Spotlight.
    "spotlight": (
        "home\n"
        "swipe 0.5 0.35 0.5 0.8 0.35\n"
        "wait 1.0"
    ),
    # Mở app theo tên hiển thị: tìm trong Spotlight rồi Enter mở kết quả đầu.
    "openapp": (
        "spotlight\n"
        "text {name}\n"
        "wait 1.5\n"
        "key Return\n"
        "wait 2.0"
    ),
    # Đóng app đang mở: vào switcher rồi hất thẻ đầu tiên lên.
    "closeapp": (
        "switcher\n"
        "swipe 0.5 0.5 0.5 0.03 0.3\n"
        "wait 0.8\n"
        "home"
    ),
    # Đóng nhiều app: ở lại trong switcher, hất liên tiếp {name} thẻ.
    "closeall": (
        "switcher\n"
        "repeat {name}\n"
        "    swipe 0.5 0.5 0.5 0.03 0.3\n"
        "    wait 0.7\n"
        "home"
    ),
    # App Library là trang ngoài cùng bên phải — nơi thấy hết app đã cài.
    "applibrary": (
        "home\n"
        "repeat 8\n"
        "    swipe 0.85 0.5 0.15 0.5 0.2\n"
        "    wait 0.35\n"
        "wait 1.0"
    ),
}

# Macro nào nhận tham số, và tham số đó là gì.
GESTURE_PARAM: Dict[str, str] = {
    "openapp": "tên app hiển thị trên máy",
    "closeall": "số thẻ cần đóng",
}

# Macro nào chỉ dùng nút cứng -> không phụ thuộc toạ độ, không cần hiệu chỉnh.
COORDINATE_FREE = frozenset({"home", "switcher", "lock"})


def load_gestures(path: Path | str = GESTURES_PATH) -> Dict[str, str]:
    """Macro mặc định, ghi đè bằng config/gestures.json nếu có."""

    gestures = dict(DEFAULT_GESTURES)
    path = Path(path)
    if path.exists():
        try:
            custom = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} không phải JSON hợp lệ: {exc}") from None
        if not isinstance(custom, dict):
            raise ValueError(f"{path} phải là một object JSON tên -> kịch bản")
        for name, body in custom.items():
            if not isinstance(body, str):
                raise ValueError(f"{path}: macro {name!r} phải là chuỗi")
            if name.lower() in RESERVED:
                raise ValueError(
                    f"{path}: {name!r} là lệnh có sẵn, không đặt tên macro trùng"
                )
            gestures[name.lower()] = body
    return gestures


def write_default_gestures(path: Path | str = GESTURES_PATH) -> Path:
    """Ghi bản mặc định ra file để người dùng chỉnh toạ độ cho máy của họ."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(DEFAULT_GESTURES, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def body_for(name: str, param: str, gestures: Dict[str, str] | None = None) -> str:
    """Kịch bản con của macro, đã thay tham số."""

    gestures = gestures if gestures is not None else load_gestures()
    if name not in gestures:
        raise KeyError(name)
    return gestures[name].replace("{name}", param)


def names(gestures: Dict[str, str] | None = None) -> List[str]:
    return sorted((gestures if gestures is not None else load_gestures()).keys())
