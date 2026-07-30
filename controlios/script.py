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

Ngoài ra còn các lệnh cử chỉ iOS dựng sẵn trong :mod:`controlios.gestures`:
``home``, ``switcher``, ``spotlight``, ``openapp <tên>``, ``closeapp``,
``closeall <số>``, ``applibrary``. Chúng được khai triển thành các lệnh nguyên
thuỷ ở trên ngay lúc phân tích cú pháp.

Dòng trống và dòng bắt đầu bằng # bị bỏ qua.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from . import gestures as gesture_lib

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
            if step.op == "repeat":
                if index < len(lines) and lines[index][1] > indent:
                    step.body, index = block(index, lines[index][1], depth + 1)
                if not step.body:
                    raise ScriptError(line_no, "repeat phải có khối thụt lề bên dưới")
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
            raise ScriptError(line_no, "cú pháp: wait <giây>")
        return Step(op, (_seconds(args[0], line_no),), line_no=line_no), index

    if op == "shot":
        return Step(op, (args[0] if args else "",), line_no=line_no), index

    if op == "repeat":
        if len(args) != 1 or not args[0].isdigit() or int(args[0]) < 1:
            raise ScriptError(line_no, "cú pháp: repeat <số lần ≥ 1>")
        return Step(op, (int(args[0]),), line_no=line_no), index

    known = ", ".join(["tap", "button", "swipe", "text", "key", "wait", "shot", "repeat"]
                      + sorted(gestures))
    raise ScriptError(line_no, f"lệnh không hiểu: {parts[0]!r}. Lệnh có: {known}")


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
            elif step.op == "text":
                out.append(f"{indent}gõ {step.args[0]!r}")
            elif step.op == "key":
                out.append(f"{indent}nhấn {' '.join(step.args)}")
            elif step.op == "wait":
                out.append(f"{indent}chờ {step.args[0]}s")
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
        elif step.op == "macro":
            total += count_steps(step.body)
        else:
            total += 1
    return total


# --------------------------------------------------------------------- runner

ScriptEvent = Callable[[str, str], None]     # key, message


async def run_on_session(session, steps: Sequence[Step], on_event: ScriptEvent,
                         shot_handler: Optional[Callable] = None,
                         cancel: Optional[asyncio.Event] = None) -> None:
    """Chạy kịch bản trên một phiên. Toạ độ tỉ lệ đổi sang pixel theo máy đó."""

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
            elif step.op == "text":
                session.type_text(step.args[0])
                await asyncio.sleep(0.05)
            elif step.op == "key":
                session.press_keys(*step.args)
                await asyncio.sleep(0.05)
            elif step.op == "wait":
                await asyncio.sleep(step.args[0])
            elif step.op == "shot":
                frame = await session.request_capture()
                if shot_handler:
                    await shot_handler(session.spec, frame, step.args[0])
            elif step.op == "repeat":
                for _ in range(step.args[0]):
                    await execute(step.body)
                continue
            elif step.op == "macro":
                name, param = step.args
                on_event(session.spec.key,
                         f"dòng {step.line_no}: {name}{' ' + param if param else ''}")
                await execute(step.body)
                continue

            on_event(session.spec.key, f"dòng {step.line_no}: {step.op}")

    await execute(steps)
