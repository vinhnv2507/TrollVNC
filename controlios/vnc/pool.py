"""Runs every VncSession on one asyncio loop in a background thread.

All public methods are safe to call from the Qt thread; they marshal onto the
loop with call_soon_threadsafe / run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence

from ..config import DeviceSpec, Settings
from ..util.png import encode_png
from .session import FrameSink, State, StatusSink, Tier, VncSession

log = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str) -> str:
    """Tên máy -> tên thư mục/file an toàn trên Windows."""
    return _UNSAFE.sub("-", text).strip("-") or "device"


def _write_capture(folder: Path, spec: DeviceSpec, frame, label: str = "",
                   stamped: bool = True) -> Path:
    """Chạy trong thread riêng — nén PNG là việc nặng, không để nghẽn event loop."""

    parts = [_slug(spec.name)]
    if stamped:
        parts.append(time.strftime("%Y%m%d-%H%M%S"))
    if label:
        parts.append(_slug(label))
    path = folder / ("_".join(parts) + ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(frame.data, frame.width, frame.height))
    return path


class DevicePool:
    def __init__(self, settings: Settings, on_frame: FrameSink, on_status: StatusSink) -> None:
        self.settings = settings
        self._on_frame = on_frame
        self._on_status = on_status

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._sessions: Dict[str, VncSession] = {}
        self._sem: Optional[asyncio.Semaphore] = None
        self._recordings: Dict[str, asyncio.Event] = {}
        self._script_cancel: Optional[asyncio.Event] = None

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run_loop, name="vnc-pool", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._sem = asyncio.Semaphore(self.settings.connect_concurrency)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def stop(self) -> None:
        if not self._loop:
            return
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            fut.result(timeout=10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self._loop = None

    async def _shutdown(self) -> None:
        await asyncio.gather(
            *(s.stop() for s in list(self._sessions.values())), return_exceptions=True
        )
        self._sessions.clear()

    # ----------------------------------------------------------------- devices

    def set_devices(self, specs: Sequence[DeviceSpec]) -> None:
        self._call(self._set_devices, specs)

    def _set_devices(self, specs: Sequence[DeviceSpec]) -> None:
        wanted = {s.key: s for s in specs if s.enabled}
        for key in list(self._sessions):
            if key not in wanted:
                session = self._sessions.pop(key)
                asyncio.create_task(session.stop())

        cap = self.settings.max_connected or len(wanted)
        for i, (key, spec) in enumerate(wanted.items()):
            if key in self._sessions:
                continue
            if i >= cap:
                break
            session = VncSession(
                spec, self.settings, self._sem, self._on_frame, self._on_status
            )
            self._sessions[key] = session
            session.start()

    # ------------------------------------------------------------------- tiers

    def set_tiers(self, tiers: Mapping[str, Tier]) -> None:
        """Bulk update, called on every scroll/selection change."""
        self._call(self._set_tiers, tiers)

    def _set_tiers(self, tiers: Mapping[str, Tier]) -> None:
        for key, session in self._sessions.items():
            session.set_tier(tiers.get(key, Tier.IDLE))

    # ------------------------------------------------------------------- input

    def tap(self, key: str, x: int, y: int, button: int = 0) -> None:
        self._call(lambda: self._with(key, lambda s: s.tap(x, y, button)))

    def mouse_down(self, key: str, x: int, y: int, button: int = 0) -> None:
        self._call(lambda: self._with(key, lambda s: s.mouse_down(x, y, button)))

    def mouse_move(self, key: str, x: int, y: int) -> None:
        self._call(lambda: self._with(key, lambda s: s.mouse_move(x, y)))

    def mouse_up(self, key: str, x: int, y: int, button: int = 0) -> None:
        self._call(lambda: self._with(key, lambda s: s.mouse_up(x, y, button)))

    def scroll(self, key: str, x: int, y: int, dx: int, dy: int) -> None:
        self._call(lambda: self._with(key, lambda s: s.scroll(x, y, dx, dy)))

    def type_text(self, keys: Iterable[str], text: str,
                  on_skipped: Optional[Callable[[str, str], None]] = None) -> None:
        """Gõ chữ vào nhiều máy. on_skipped(key, chars) khi có ký tự không gửi được."""

        key_list = list(keys)

        def run() -> None:
            for key in key_list:
                session = self._sessions.get(key)
                if session is None:
                    continue
                skipped = session.type_text(text)
                if skipped and on_skipped:
                    on_skipped(key, skipped)

        self._call(run)

    def press_keys(self, keys: Iterable[str], *names: str) -> None:
        self._call(lambda: [self._with(k, lambda s: s.press_keys(*names)) for k in list(keys)])

    def broadcast_tap(self, keys: Iterable[str], rx: float, ry: float) -> None:
        """Tap the same relative point on many phones at once.

        Coordinates are ratios (0..1) so a mixed fleet of screen sizes still
        lands on the same UI element.
        """

        key_list = list(keys)

        def run() -> None:
            for key in key_list:
                session = self._sessions.get(key)
                if not session or session.state is not State.ONLINE:
                    continue
                client = session._client
                if not client:
                    continue
                session.tap(int(client.video.width * rx), int(client.video.height * ry))

        self._call(run)

    def broadcast_scroll(self, keys: Iterable[str], rx: float, ry: float,
                         dx: int, dy: int) -> None:
        key_list = list(keys)

        def run() -> None:
            for key in key_list:
                session = self._sessions.get(key)
                if not session or not session._client:
                    continue
                video = session._client.video
                session.scroll(int(video.width * rx), int(video.height * ry), dx, dy)

        self._call(run)

    def broadcast_swipe(self, keys: Iterable[str], r1: tuple[float, float],
                        r2: tuple[float, float], duration: float = 0.25) -> None:
        key_list = list(keys)

        async def run() -> None:
            tasks = []
            for key in key_list:
                session = self._sessions.get(key)
                if not session or not session._client:
                    continue
                w, h = session._client.video.width, session._client.video.height
                tasks.append(
                    session.swipe(
                        int(w * r1[0]), int(h * r1[1]),
                        int(w * r2[0]), int(h * r2[1]), duration,
                    )
                )
            await asyncio.gather(*tasks, return_exceptions=True)

        self._call_coro(run())

    # ----------------------------------------------------------------- capture

    def capture(self, keys: Iterable[str], folder: Path | str,
                suffix: str = "", on_event=None) -> None:
        """Chụp ảnh full độ phân giải nhiều máy cùng lúc, ghi ra PNG.

        on_event(key, path_or_None, error_or_None) chạy trên luồng mạng.
        """

        key_list = list(keys)
        folder = Path(folder)

        async def run() -> None:
            await asyncio.gather(
                *(self._capture_one(k, folder, suffix, on_event) for k in key_list),
                return_exceptions=True,
            )

        self._call_coro(run())

    async def _capture_one(self, key: str, folder: Path, suffix: str, on_event) -> None:
        session = self._sessions.get(key)
        if session is None:
            if on_event:
                on_event(key, None, "không có phiên")
            return
        try:
            frame = await session.request_capture()
            path = await asyncio.to_thread(
                _write_capture, folder, session.spec, frame, suffix
            )
            if on_event:
                on_event(key, str(path), None)
        except Exception as exc:
            if on_event:
                on_event(key, None, f"{type(exc).__name__}: {exc}")

    # --------------------------------------------------------------- recording

    def start_recording(self, keys: Iterable[str], folder: Path | str,
                        fps: float = 2.0, on_event=None) -> str:
        """Ghi hình dạng chuỗi ảnh PNG. Trả về id để dừng."""

        key_list = list(keys)
        folder = Path(folder)
        session_id = f"rec-{int(time.time())}"

        async def run() -> None:
            stop = asyncio.Event()
            self._recordings[session_id] = stop
            try:
                await asyncio.gather(
                    *(self._record_one(k, folder / session_id, fps, stop, on_event)
                      for k in key_list),
                    return_exceptions=True,
                )
            finally:
                self._recordings.pop(session_id, None)
                if on_event:
                    on_event(session_id, None, "đã dừng")

        self._call_coro(run())
        return session_id

    def stop_recording(self, session_id: Optional[str] = None) -> None:
        def run() -> None:
            targets = ([session_id] if session_id else list(self._recordings))
            for name in targets:
                stop = self._recordings.get(name)
                if stop:
                    stop.set()

        self._call(run)

    def is_recording(self) -> bool:
        return bool(self._recordings)

    async def _record_one(self, key: str, folder: Path, fps: float,
                          stop: asyncio.Event, on_event) -> None:
        session = self._sessions.get(key)
        if session is None:
            return
        interval = 1.0 / max(fps, 0.1)
        index = 0
        while not stop.is_set():
            started = time.monotonic()
            try:
                frame = await session.request_capture()
                index += 1
                await asyncio.to_thread(
                    _write_capture, folder / _slug(session.spec.name),
                    session.spec, frame, f"{index:06d}", stamped=False,
                )
            except Exception as exc:
                if on_event:
                    on_event(key, None, f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(1.0)
                continue
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass

    # ------------------------------------------------------- kênh điều khiển

    def _channel(self, key: str):
        from ..control_channel import ControlChannel

        session = self._sessions.get(key)
        host = session.spec.host if session else key.partition(":")[0]
        return ControlChannel(
            host, self.settings.control_port, self.settings.control_token
        )

    def list_apps(self, key: str, on_done, user_only: bool = False) -> None:
        """on_done(key, apps, error) — chạy trên luồng mạng."""

        async def run() -> None:
            try:
                apps = await self._channel(key).list_apps(user_only=user_only)
                on_done(key, apps, None)
            except Exception as exc:
                on_done(key, [], str(exc))

        self._call_coro(run())

    def launch_app(self, keys: Iterable[str], bundle_id: str, on_event=None) -> None:
        key_list = list(keys)

        async def run() -> None:
            async def one(key: str) -> None:
                try:
                    await self._channel(key).launch(bundle_id)
                    if on_event:
                        on_event(key, f"đã mở {bundle_id}")
                except Exception as exc:
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)

        self._call_coro(run())

    def terminate_app(self, keys: Iterable[str], bundle_id: str, on_event=None) -> None:
        key_list = list(keys)

        async def run() -> None:
            async def one(key: str) -> None:
                try:
                    closed = await self._channel(key).terminate(bundle_id)
                    if on_event:
                        on_event(key, f"đã đóng {bundle_id}" if closed
                                 else f"{bundle_id} vốn không chạy")
                except Exception as exc:
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)

        self._call_coro(run())

    # ------------------------------------------------------------------ script

    def run_script(self, keys: Iterable[str], steps, folder: Path | str,
                   on_event=None, on_done=None) -> None:
        """Chạy kịch bản song song trên các máy đã chọn."""

        from ..script import run_on_session

        key_list = list(keys)
        folder = Path(folder)

        async def run() -> None:
            self._script_cancel = asyncio.Event()

            async def shot_handler(spec, frame, label):
                await asyncio.to_thread(_write_capture, folder, spec, frame, label)

            async def one(key: str) -> None:
                session = self._sessions.get(key)
                if session is None or session.state is not State.ONLINE:
                    if on_event:
                        on_event(key, "bỏ qua: chưa kết nối")
                    return
                try:
                    await run_on_session(
                        session, steps,
                        on_event or (lambda k, m: None),
                        shot_handler, self._script_cancel,
                        control=self._channel(key) if self.settings.control_token else None,
                    )
                    if on_event:
                        on_event(key, "xong")
                except asyncio.CancelledError:
                    if on_event:
                        on_event(key, "đã huỷ")
                except Exception as exc:
                    if on_event:
                        on_event(key, f"LỖI {type(exc).__name__}: {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)
            self._script_cancel = None
            if on_done:
                on_done()

        self._call_coro(run())

    def cancel_script(self) -> None:
        def run() -> None:
            if self._script_cancel:
                self._script_cancel.set()

        self._call(run)

    # ------------------------------------------------------------------- utils

    def stats(self) -> dict:
        counts = {s.value: 0 for s in State}
        for session in list(self._sessions.values()):
            counts[session.state.value] += 1
        counts["total"] = len(self._sessions)
        return counts

    def _with(self, key: str, fn) -> None:
        session = self._sessions.get(key)
        if session:
            fn(session)

    def _call(self, fn, *args) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: fn(*args))

    def _call_coro(self, coro) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            coro.close()
