"""Kịch bản thao tác — một ngôn ngữ nhỏ chạy song song trên nhiều máy.

Toạ độ luôn là **tỉ lệ 0..1** chứ không phải pixel, nên cùng một kịch bản chạy
đúng trên các iPhone khác kích thước màn hình.

    tap 0.5 0.85                  # chạm giữa màn hình, 85% chiều cao
    swipe 0.5 0.8 0.5 0.2 0.3     # vuốt lên trong 0.3 giây
    swipe 0.5 0.99 0.5 0.45 0.35 0.7   # ... rồi giữ 0.7s trước khi nhả
    text Xin chào
    key Return
    wait 1.5
    shot ket-qua                  # chụp màn hình, tên file có hậu tố ket-qua
    repeat 3                      # lặp lại khối thụt lề bên dưới
        tap 0.5 0.9
        wait 1
    retry 3 1                     # lỗi thì thử lại tối đa 3 lần, cách nhau 1s
        restartapp com.zing.zalo 2

Ngoài ra còn các lệnh cử chỉ iOS dựng sẵn trong :mod:`controlios.gestures`:
``home``, ``switcher``, ``spotlight``, ``openapp <tên>``, ``closeapp``,
``closeall <số>``, ``applibrary``. Chúng được khai triển thành các lệnh nguyên
thuỷ ở trên ngay lúc phân tích cú pháp.

Dòng trống và dòng bắt đầu bằng # bị bỏ qua.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from . import gestures as gesture_lib
from .vnc.session import BRIGHTNESS_STEPS

MAX_DEPTH = 5
MAX_MACRO_DEPTH = 6

# TrollVNC map nút chuột thành nút cứng của máy (theo README của nó):
#   chuột phải  -> nút Home/Menu
#   chuột giữa  -> nút Power
# Nên `button home` là một cú chuột phải, chắc chắn hơn vuốt mò toạ độ.
BUTTON_NAMES = {
    "left": 0,
    "middle": 1,
    "right": 2,
    "home": 2,
    "power": 1,
}


class ScriptError(ValueError):
    """Lỗi cú pháp, kèm số dòng."""

    def __init__(self, line_no: int, message: str) -> None:
        super().__init__(f"dòng {line_no}: {message}")
        self.line_no = line_no


@dataclass
class Step:
    op: str
    args: tuple = ()
    body: List["Step"] = field(default_factory=list)
    line_no: int = 0


def _ratio(raw: str, line_no: int) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ScriptError(line_no, f"{raw!r} không phải số") from None
    if not 0.0 <= value <= 1.0:
        raise ScriptError(line_no, f"toạ độ {value} phải nằm trong 0..1 (tỉ lệ màn hình)")
    return value


def parse(source: str, gestures: Optional[Dict[str, str]] = None,
          _macro_depth: int = 0) -> List[Step]:
    """Text -> danh sách Step. Khối lồng nhau xác định bằng thụt lề.

    Macro cử chỉ được khai triển ngay tại đây, nên lỗi trong macro lộ ra lúc
    bấm Kiểm tra chứ không phải giữa chừng khi đang chạy trên 250 máy.
    """

    if gestures is None:
        gestures = gesture_lib.load_gestures()

    lines = []
    for line_no, raw in enumerate(source.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        lines.append((line_no, indent, stripped))

    def block(index: int, indent: int, depth: int) -> tuple[List[Step], int]:
        if depth > MAX_DEPTH:
            raise ScriptError(lines[index][0], "lồng repeat quá sâu")
        steps: List[Step] = []
        while index < len(lines):
            line_no, line_indent, text = lines[index]
            if line_indent < indent:
                break
            if line_indent > indent:
                raise ScriptError(line_no, "thụt lề không khớp")
            step, index = _statement(line_no, text, index + 1, gestures, _macro_depth)
            steps.append(step)
            if step.op in ("repeat", "retry"):
                if index < len(lines) and lines[index][1] > indent:
                    step.body, index = block(index, lines[index][1], depth + 1)
                if not step.body:
                    raise ScriptError(line_no, f"{step.op} phải có khối thụt lề bên dưới")
        return steps, index

    steps, consumed = block(0, lines[0][1] if lines else 0, 0)
    if consumed < len(lines):
        raise ScriptError(lines[consumed][0], "thụt lề không khớp")
    return steps


def _statement(line_no: int, text: str, index: int, gestures: Dict[str, str],
               macro_depth: int) -> tuple[Step, int]:
    parts = text.split()
    op = parts[0].lower()
    args = parts[1:]

    if op in gestures:
        return _macro(line_no, text, op, args, gestures, macro_depth), index

    if op == "tap":
        if len(args) != 2:
            raise ScriptError(line_no, "cú pháp: tap <x> <y>")
        return Step(op, (_ratio(args[0], line_no), _ratio(args[1], line_no)), line_no=line_no), index

    if op == "swipe":
        if len(args) not in (4, 5, 6):
            raise ScriptError(
                line_no, "cú pháp: swipe <x1> <y1> <x2> <y2> [giây] [giữ]"
            )
        coords = tuple(_ratio(a, line_no) for a in args[:4])
        duration = _seconds(args[4], line_no) if len(args) >= 5 else 0.3
        hold = _seconds(args[5], line_no) if len(args) == 6 else 0.0
        return Step(op, coords + (duration, hold), line_no=line_no), index

    if op == "controlcenter":
        if args:
            raise ScriptError(line_no, "cú pháp: controlcenter")
        return Step(op, line_no=line_no), index

    if op == "button":
        if not args or args[0].lower() not in BUTTON_NAMES:
            raise ScriptError(
                line_no,
                f"cú pháp: button <{'|'.join(BUTTON_NAMES)}> [x] [y]",
            )
        number = BUTTON_NAMES[args[0].lower()]
        if len(args) == 3:
            x, y = _ratio(args[1], line_no), _ratio(args[2], line_no)
        elif len(args) == 1:
            x, y = 0.5, 0.5
        else:
            raise ScriptError(line_no, "cú pháp: button <tên> [x] [y]")
        return Step(op, (number, x, y, args[0].lower()), line_no=line_no), index

    if op in ("launchapp", "killapp"):
        bundle = text[len(parts[0]):].strip()
        if not bundle:
            raise ScriptError(line_no, f"cú pháp: {op} <bundle id>, ví dụ com.zing.zalo")
        if " " in bundle:
            raise ScriptError(line_no, f"bundle id không được có dấu cách: {bundle!r}")
        if "." not in bundle:
            raise ScriptError(
                line_no,
                f"{bundle!r} không giống bundle id. Đây là lệnh dùng kênh điều khiển; "
                f"muốn mở theo tên hiển thị thì dùng: openapp {bundle}",
            )
        return Step(op, (bundle,), line_no=line_no), index

    if op == "restartapp":
        if len(args) not in (1, 2):
            raise ScriptError(line_no, "cú pháp: restartapp <bundle id> [giây chờ]")
        bundle = args[0]
        if "." not in bundle:
            raise ScriptError(line_no, f"{bundle!r} không giống bundle id")
        delay = _seconds(args[1], line_no) if len(args) == 2 else 1.0
        return Step(op, (bundle, delay), line_no=line_no), index

    if op == "openurl":
        url = text[len(parts[0]):].strip()
        if not url or "://" not in url or " " in url:
            raise ScriptError(line_no, "cú pháp: openurl <url>, URL không được có dấu cách")
        return Step(op, (url,), line_no=line_no), index

    if op == "openurlin":
        if len(args) != 2 or "." not in args[0] or "://" not in args[1]:
            raise ScriptError(line_no, "cú pháp: openurlin <bundle id> <url>")
        return Step(op, (args[0], args[1]), line_no=line_no), index

    if op == "clipboard":
        payload = text[len(parts[0]):].strip()
        if not payload:
            raise ScriptError(line_no, "cú pháp: clipboard <nội dung>")
        return Step(op, (payload,), line_no=line_no), index

    if op == "savephoto":
        path = text[len(parts[0]):].strip()
        if not path or not path.startswith("/"):
            raise ScriptError(
                line_no, "cú pháp: savephoto <đường dẫn tuyệt đối ảnh trên máy>"
            )
        return Step(op, (path,), line_no=line_no), index

    if op == "wipeapp":
        if len(args) != 1 or "." not in args[0]:
            raise ScriptError(line_no, "cú pháp: wipeapp <bundle id>, ví dụ com.zing.zalo")
        return Step(op, (args[0],), line_no=line_no), index

    if op == "snapshot":
        if len(args) not in (1, 2) or "." not in args[0]:
            raise ScriptError(line_no, "cú pháp: snapshot <bundle id> [tên]")
        name = args[1] if len(args) == 2 else ""
        return Step(op, (args[0], name), line_no=line_no), index

    if op == "restore":
        if len(args) != 2 or "." not in args[0]:
            raise ScriptError(line_no, "cú pháp: restore <bundle id> <tên snapshot>")
        return Step(op, (args[0], args[1]), line_no=line_no), index

    if op == "text":
        payload = text[len(parts[0]):].strip()
        if not payload:
            raise ScriptError(line_no, "cú pháp: text <nội dung>")
        return Step(op, (payload,), line_no=line_no), index

    if op == "key":
        if len(args) < 1:
            raise ScriptError(line_no, "cú pháp: key <tên phím>")
        return Step(op, tuple(args), line_no=line_no), index

    if op == "wait":
        if len(args) != 1:
            raise ScriptError(line_no, "cú pháp: wait <giây> hoặc wait <từ>-<đến>")
        return Step(op, _wait_range(args[0], line_no), line_no=line_no), index

    if op in ("brightness", "volume"):
        return _media(line_no, op, args), index

    if op == "shot":
        return Step(op, (args[0] if args else "",), line_no=line_no), index

    if op == "repeat":
        if len(args) != 1 or not args[0].isdigit() or int(args[0]) < 1:
            raise ScriptError(line_no, "cú pháp: repeat <số lần ≥ 1>")
        return Step(op, (int(args[0]),), line_no=line_no), index

    if op == "retry":
        if len(args) not in (1, 2) or not args[0].isdigit() or int(args[0]) < 1:
            raise ScriptError(line_no, "cú pháp: retry <số lần ≥ 1> [giây nghỉ]")
        delay = _seconds(args[1], line_no) if len(args) == 2 else 1.0
        return Step(op, (int(args[0]), delay), line_no=line_no), index

    known = ", ".join(["tap", "button", "swipe", "controlcenter", "text", "key", "wait", "shot",
                       "repeat", "retry", "brightness", "volume", "launchapp",
                       "killapp", "restartapp", "openurl", "openurlin",
                       "clipboard", "savephoto", "wipeapp", "snapshot",
                       "restore"]
                      + sorted(gestures))
    raise ScriptError(line_no, f"lệnh không hiểu: {parts[0]!r}. Lệnh có: {known}")


def _wait_range(raw: str, line_no: int) -> tuple:
    """``5`` -> (5, 5); ``5-10`` -> (5, 10), chờ ngẫu nhiên trong khoảng.

    Chờ ngẫu nhiên là thứ đáng dùng khi chạy trên nhiều máy: mỗi máy bốc một
    con số riêng nên chúng không thao tác đồng loạt cùng một nhịp.
    """

    if "-" in raw[1:]:                       # bỏ qua dấu trừ ở đầu (số âm)
        low_text, _, high_text = raw.partition("-")
        low = _seconds(low_text, line_no)
        high = _seconds(high_text, line_no)
        if high < low:
            raise ScriptError(line_no, f"khoảng chờ ngược: {raw}")
        return (low, high)
    value = _seconds(raw, line_no)
    return (value, value)


# Tên lệnh -> (tên keysym khi tăng, khi giảm)
_MEDIA_DIRECTIONS = {
    "brightness": ("brightness_up", "brightness_down"),
    "volume": ("volume_up", "volume_down"),
}


def _media(line_no: int, op: str, args: List[str]) -> Step:
    """``brightness down 4`` / ``brightness min`` / ``volume mute``."""

    up_key, down_key = _MEDIA_DIRECTIONS[op]
    if not args:
        raise ScriptError(line_no, f"cú pháp: {op} up|down|min|max [số nấc]")

    action = args[0].lower()
    steps_text = args[1] if len(args) > 1 else None

    if op == "volume" and action == "mute":
        return Step(op, ("mute", 1, "mute"), line_no=line_no)

    if action in ("min", "max"):
        if steps_text:
            raise ScriptError(line_no, f"{op} {action} không nhận thêm số nấc")
        key = down_key if action == "min" else up_key
        return Step(op, (key, BRIGHTNESS_STEPS, action), line_no=line_no)

    if action not in ("up", "down"):
        allowed = "up|down|min|max" + ("|mute" if op == "volume" else "")
        raise ScriptError(line_no, f"cú pháp: {op} {allowed} [số nấc]")

    repeat = 1
    if steps_text is not None:
        if not steps_text.isdigit() or int(steps_text) < 1:
            raise ScriptError(line_no, f"số nấc phải là số nguyên ≥ 1: {steps_text!r}")
        repeat = int(steps_text)

    return Step(op, (up_key if action == "up" else down_key, repeat, action),
                line_no=line_no)


def _seconds(raw: str, line_no: int) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ScriptError(line_no, f"{raw!r} không phải số giây") from None
    if value < 0:
        raise ScriptError(line_no, "thời gian không được âm")
    return value


def _macro(line_no: int, text: str, name: str, args: List[str],
           gestures: Dict[str, str], macro_depth: int) -> Step:
    """Khai triển một cử chỉ dựng sẵn thành các lệnh nguyên thuỷ."""

    if macro_depth >= MAX_MACRO_DEPTH:
        raise ScriptError(line_no, f"macro {name!r} lồng nhau quá sâu (vòng lặp?)")

    param = text[len(name):].strip()
    if name in gesture_lib.GESTURE_PARAM and not param:
        raise ScriptError(
            line_no, f"cú pháp: {name} <{gesture_lib.GESTURE_PARAM[name]}>"
        )

    body_source = gesture_lib.body_for(name, param, gestures)
    try:
        body = parse(body_source, gestures, macro_depth + 1)
    except ScriptError as exc:
        raise ScriptError(
            line_no, f"macro {name!r} hỏng ở dòng {exc.line_no} của nó: {exc}"
        ) from None
    if not body:
        raise ScriptError(line_no, f"macro {name!r} rỗng")
    return Step("macro", (name, param), body=body, line_no=line_no)


_MACRO_LABELS = {
    "home": "về màn hình chính",
    "switcher": "mở trình chuyển app",
    "spotlight": "mở tìm kiếm Spotlight",
    "openapp": "mở app «{param}»",
    "closeapp": "đóng app đang mở",
    "closeall": "đóng {param} app gần đây",
    "applibrary": "mở App Library",
}


def describe(steps: Sequence[Step]) -> List[str]:
    """Diễn giải lại kịch bản để hiển thị trước khi chạy."""

    out: List[str] = []

    def walk(items: Sequence[Step], indent: str) -> None:
        for step in items:
            if step.op == "repeat":
                out.append(f"{indent}lặp {step.args[0]} lần:")
                walk(step.body, indent + "  ")
            elif step.op == "retry":
                attempts, delay = step.args
                out.append(f"{indent}thử lại tối đa {attempts} lần, nghỉ {delay}s:")
                walk(step.body, indent + "  ")
            elif step.op == "macro":
                name, param = step.args
                label = _MACRO_LABELS.get(name, f"cử chỉ {name}")
                out.append(f"{indent}{label.format(param=param)}")
            elif step.op == "tap":
                out.append(f"{indent}chạm ({step.args[0]:.0%}, {step.args[1]:.0%})")
            elif step.op == "button":
                out.append(f"{indent}nhấn nút {step.args[3]}")
            elif step.op == "swipe":
                x1, y1, x2, y2, duration, hold = step.args
                held = f", giữ {hold}s" if hold else ""
                out.append(
                    f"{indent}vuốt ({x1:.0%},{y1:.0%}) → ({x2:.0%},{y2:.0%}) "
                    f"trong {duration}s{held}"
                )
            elif step.op == "controlcenter":
                out.append(f"{indent}mở Trung tâm điều khiển")
            elif step.op == "launchapp":
                out.append(f"{indent}mở app {step.args[0]} (qua kênh điều khiển)")
            elif step.op == "killapp":
                out.append(f"{indent}đóng app {step.args[0]} (qua kênh điều khiển)")
            elif step.op == "restartapp":
                out.append(f"{indent}khởi động lại app {step.args[0]}, chờ {step.args[1]}s")
            elif step.op == "openurl":
                out.append(f"{indent}mở URL {step.args[0]}")
            elif step.op == "openurlin":
                out.append(f"{indent}mở URL {step.args[1]} bằng app {step.args[0]}")
            elif step.op == "clipboard":
                out.append(f"{indent}đặt clipboard máy = {step.args[0]!r}")
            elif step.op == "savephoto":
                out.append(f"{indent}nạp {step.args[0]} vào Thư viện Ảnh")
            elif step.op == "wipeapp":
                out.append(f"{indent}xoá dữ liệu app {step.args[0]} (như cài lại)")
            elif step.op == "snapshot":
                label = f" tên “{step.args[1]}”" if len(step.args) > 1 and step.args[1] else " (tên tự sinh)"
                out.append(f"{indent}lưu snapshot dữ liệu app {step.args[0]}{label}")
            elif step.op == "restore":
                out.append(f"{indent}khôi phục dữ liệu app {step.args[0]} về snapshot “{step.args[1]}”")
            elif step.op == "text":
                out.append(f"{indent}gõ {step.args[0]!r}")
            elif step.op == "key":
                out.append(f"{indent}nhấn {' '.join(step.args)}")
            elif step.op == "wait":
                low, high = step.args
                out.append(f"{indent}chờ {low}s" if high <= low
                           else f"{indent}chờ ngẫu nhiên {low}–{high}s")
            elif step.op in ("brightness", "volume"):
                _key, repeat, action = step.args
                label = {"brightness": "độ sáng", "volume": "âm lượng"}[step.op]
                words = {"up": "tăng", "down": "giảm", "min": "xuống thấp nhất",
                         "max": "lên cao nhất", "mute": "tắt tiếng"}
                suffix = f" {repeat} nấc" if action in ("up", "down") and repeat > 1 else ""
                out.append(f"{indent}{words[action]} {label}{suffix}")
            elif step.op == "shot":
                out.append(f"{indent}chụp màn hình")

    walk(steps, "")
    return out


def count_steps(steps: Sequence[Step]) -> int:
    """Tổng số lệnh sẽ thực thi, đã tính cả vòng lặp."""

    total = 0
    for step in steps:
        if step.op == "repeat":
            total += step.args[0] * count_steps(step.body)
        elif step.op == "retry":
            # Hiển thị trường hợp xấu nhất để người dùng biết trần công việc.
            total += step.args[0] * count_steps(step.body)
        elif step.op == "macro":
            total += count_steps(step.body)
        else:
            total += 1
    return total


# --------------------------------------------------------------------- runner

ScriptEvent = Callable[[str, str], None]     # key, message


async def run_on_session(session, steps: Sequence[Step], on_event: ScriptEvent,
                         shot_handler: Optional[Callable] = None,
                         cancel: Optional[asyncio.Event] = None,
                         control=None) -> None:
    """Chạy kịch bản trên một phiên. Toạ độ tỉ lệ đổi sang pixel theo máy đó.

    ``control`` là :class:`~controlios.control_channel.ControlChannel` của đúng
    máy đó, chỉ cần cho các lệnh app/URL theo bundle id.
    """

    client = session._client
    if client is None:
        raise ConnectionError(f"{session.spec.key} chưa kết nối")

    async def execute(items: Sequence[Step]) -> None:
        for step in items:
            if cancel and cancel.is_set():
                raise asyncio.CancelledError()
            width, height = client.video.width, client.video.height

            if step.op == "tap":
                x, y = step.args
                session.tap(int(x * width), int(y * height))
                await asyncio.sleep(0.05)
            elif step.op == "button":
                number, x, y, _name = step.args
                session.tap(int(x * width), int(y * height), number)
                await asyncio.sleep(0.05)
            elif step.op == "swipe":
                x1, y1, x2, y2, duration, hold = step.args
                await session.swipe(int(x1 * width), int(y1 * height),
                                    int(x2 * width), int(y2 * height), duration,
                                    hold=hold)
            elif step.op == "controlcenter":
                if control is None:
                    raise ConnectionError("lệnh controlcenter cần kênh điều khiển ControlIOS")
                await control.control_center()
            elif step.op in ("launchapp", "killapp"):
                if control is None:
                    raise ConnectionError(
                        f"lệnh {step.op} cần kênh điều khiển; đặt control_token "
                        "trong config/devices.json và dùng ControlIOS"
                    )
                if step.op == "launchapp":
                    await control.launch(step.args[0])
                else:
                    await control.terminate(step.args[0])
            elif step.op in ("restartapp", "openurl", "openurlin"):
                if control is None:
                    raise ConnectionError(
                        f"lệnh {step.op} cần kênh điều khiển; đặt control_token "
                        "trong config/devices.json và dùng ControlIOS"
                    )
                if step.op == "restartapp":
                    bundle, delay = step.args
                    await control.terminate(bundle)
                    await asyncio.sleep(delay)
                    await control.launch(bundle)
                elif step.op == "openurl":
                    await control.open_url(step.args[0])
                else:
                    await control.open_url_in(step.args[0], step.args[1])
            elif step.op in ("clipboard", "savephoto"):
                if control is None:
                    raise ConnectionError(
                        f"lệnh {step.op} cần kênh điều khiển; đặt control_token "
                        "trong config/devices.json và dùng ControlIOS"
                    )
                if step.op == "clipboard":
                    await control.set_clipboard(step.args[0])
                else:
                    await control.save_photo(step.args[0])
            elif step.op in ("wipeapp", "snapshot", "restore"):
                if control is None:
                    raise ConnectionError(
                        f"lệnh {step.op} cần kênh điều khiển; đặt control_token "
                        "trong config/devices.json và dùng ControlIOS"
                    )
                bundle = step.args[0]
                await control.terminate(bundle)   # đóng app để file được nhả trước
                if step.op == "wipeapp":
                    await control.wipe_app(bundle)
                elif step.op == "snapshot":
                    await control.snapshot_app(bundle, step.args[1])
                else:
                    await control.restore_app(bundle, step.args[1])
            elif step.op == "text":
                if control is not None:
                    await control.type_text(step.args[0])
                else:
                    # Tương thích máy ControlIOS cũ/chưa cấu hình control token.
                    session.type_text(step.args[0])
                await asyncio.sleep(0.05)
            elif step.op == "key":
                session.press_keys(*step.args)
                await asyncio.sleep(0.05)
            elif step.op == "wait":
                low, high = step.args
                # Mỗi máy bốc số riêng nên chúng không chạy đồng loạt cùng nhịp.
                await asyncio.sleep(random.uniform(low, high) if high > low else low)
            elif step.op in ("brightness", "volume"):
                key, repeat, _action = step.args
                session.media_key(key, repeat)
                await asyncio.sleep(0.05 * repeat)
            elif step.op == "shot":
                frame = await session.request_capture()
                if shot_handler:
                    await shot_handler(session.spec, frame, step.args[0])
            elif step.op == "repeat":
                for _ in range(step.args[0]):
                    await execute(step.body)
                continue
            elif step.op == "retry":
                attempts, delay = step.args
                for attempt in range(1, attempts + 1):
                    try:
                        await execute(step.body)
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if attempt >= attempts:
                            raise
                        on_event(
                            session.spec.key,
                            f"dòng {step.line_no}: lỗi {exc}; thử lại "
                            f"{attempt + 1}/{attempts} sau {delay}s",
                        )
                        await asyncio.sleep(delay)
                continue
            elif step.op == "macro":
                name, param = step.args
                on_event(session.spec.key,
                         f"dòng {step.line_no}: {name}{' ' + param if param else ''}")
                await execute(step.body)
                continue

            on_event(session.spec.key, f"dòng {step.line_no}: {step.op}")

    await execute(steps)
