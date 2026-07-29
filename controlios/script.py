"""Kịch bản thao tác — một ngôn ngữ nhỏ chạy song song trên nhiều máy.

Toạ độ luôn là **tỉ lệ 0..1** chứ không phải pixel, nên cùng một kịch bản chạy
đúng trên các iPhone khác kích thước màn hình.

    tap 0.5 0.85              # chạm giữa màn hình, 85% chiều cao
    swipe 0.5 0.8 0.5 0.2 0.3 # vuốt lên trong 0.3 giây
    text Xin chào
    key Return
    wait 1.5
    shot ket-qua              # chụp màn hình, tên file có hậu tố ket-qua
    repeat 3                  # lặp lại khối thụt lề bên dưới
        tap 0.5 0.9
        wait 1

Dòng trống và dòng bắt đầu bằng # bị bỏ qua.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

MAX_DEPTH = 5


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


def parse(source: str) -> List[Step]:
    """Text -> danh sách Step. Khối lồng nhau xác định bằng thụt lề."""

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
            step, index = _statement(line_no, text, index + 1, depth)
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


def _statement(line_no: int, text: str, index: int, depth: int) -> tuple[Step, int]:
    parts = text.split()
    op = parts[0].lower()
    args = parts[1:]

    if op == "tap":
        if len(args) != 2:
            raise ScriptError(line_no, "cú pháp: tap <x> <y>")
        return Step(op, (_ratio(args[0], line_no), _ratio(args[1], line_no)), line_no=line_no), index

    if op == "swipe":
        if len(args) not in (4, 5):
            raise ScriptError(line_no, "cú pháp: swipe <x1> <y1> <x2> <y2> [giây]")
        coords = tuple(_ratio(a, line_no) for a in args[:4])
        duration = float(args[4]) if len(args) == 5 else 0.3
        return Step(op, coords + (duration,), line_no=line_no), index

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
        try:
            seconds = float(args[0])
        except ValueError:
            raise ScriptError(line_no, f"{args[0]!r} không phải số giây") from None
        if seconds < 0:
            raise ScriptError(line_no, "thời gian chờ không được âm")
        return Step(op, (seconds,), line_no=line_no), index

    if op == "shot":
        return Step(op, (args[0] if args else "",), line_no=line_no), index

    if op == "repeat":
        if len(args) != 1 or not args[0].isdigit() or int(args[0]) < 1:
            raise ScriptError(line_no, "cú pháp: repeat <số lần ≥ 1>")
        return Step(op, (int(args[0]),), line_no=line_no), index

    raise ScriptError(line_no, f"lệnh không hiểu: {parts[0]!r}")


def describe(steps: Sequence[Step]) -> List[str]:
    """Diễn giải lại kịch bản để hiển thị trước khi chạy."""

    out: List[str] = []

    def walk(items: Sequence[Step], indent: str) -> None:
        for step in items:
            if step.op == "repeat":
                out.append(f"{indent}lặp {step.args[0]} lần:")
                walk(step.body, indent + "  ")
            elif step.op == "tap":
                out.append(f"{indent}chạm ({step.args[0]:.0%}, {step.args[1]:.0%})")
            elif step.op == "swipe":
                x1, y1, x2, y2, duration = step.args
                out.append(
                    f"{indent}vuốt ({x1:.0%},{y1:.0%}) → ({x2:.0%},{y2:.0%}) trong {duration}s"
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
            elif step.op == "swipe":
                x1, y1, x2, y2, duration = step.args
                await session.swipe(int(x1 * width), int(y1 * height),
                                    int(x2 * width), int(y2 * height), duration)
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

            on_event(session.spec.key, f"dòng {step.line_no}: {step.op}")

    await execute(steps)
