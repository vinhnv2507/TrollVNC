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
        #: key -> thời điểm bắt đầu nằm ngoài khung nhìn
        self._idle_since: Dict[str, float] = {}
        self._janitor: Optional[asyncio.Task] = None

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
        self._janitor = loop.create_task(self._idle_janitor())
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
        if self._janitor:
            self._janitor.cancel()
            self._janitor = None
        await asyncio.gather(
            *(s.stop() for s in list(self._sessions.values())), return_exceptions=True
        )
        self._sessions.clear()
        self._idle_since.clear()

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
        now = time.monotonic()
        for key, session in self._sessions.items():
            tier = tiers.get(key, Tier.IDLE)
            session.set_tier(tier)
            if tier > Tier.IDLE:
                self._idle_since.pop(key, None)
                if not session.is_running():
                    session.start()          # đánh thức máy đang ngủ
            else:
                self._idle_since.setdefault(key, now)

    async def _idle_janitor(self) -> None:
        """Ngắt kết nối tới máy đã lâu không nhìn tới.

        Cơ chế tier chỉ tiết kiệm cho phía PC: TrollVNC vẫn chạy ScreenCapturer
        chừng nào còn client nối vào. Rời hẳn mới trả được CPU cho iPhone.
        """

        while True:
            await asyncio.sleep(5)
            limit = self.settings.idle_disconnect_after
            if not limit:
                continue
            now = time.monotonic()
            for key, since in list(self._idle_since.items()):
                if now - since < limit:
                    continue
                session = self._sessions.get(key)
                if session and session.is_running():
                    await session.sleep()

    async def _ensure_awake(self, key: str, timeout: float = 15.0):
        """Đánh thức một máy đang ngủ và chờ nó online.

        Cần cho chụp ảnh, ghi hình và kịch bản: nếu không thì máy ngoài khung
        nhìn sẽ bị bỏ qua một cách khó hiểu.
        """

        session = self._sessions.get(key)
        if session is None:
            return None
        self._idle_since.pop(key, None)
        if not session.is_running():
            session.start()
        deadline = time.monotonic() + timeout
        while session.state is not State.ONLINE and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        return session

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

    def media_key(self, keys: Iterable[str], name: str, repeat: int = 1) -> None:
        """Phím độ sáng / âm lượng cho nhiều máy cùng lúc."""

        key_list = list(keys)
        self._call(lambda: [self._with(k, lambda s: s.media_key(name, repeat))
                            for k in key_list])

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
        session = await self._ensure_awake(key)
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
        session = await self._ensure_awake(key)
        if session is None:
            return
        # Ghi hình phải giữ máy tỉnh suốt buổi, không để janitor ngắt giữa chừng.
        self._idle_since.pop(key, None)
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

    def _bulk_app_action(self, keys: Iterable[str], describe, action,
                         on_event=None, on_done=None) -> None:
        """Chạy một thao tác app trên nhiều máy rồi **tổng kết lại**.

        Tổng kết là phần quan trọng: máy chưa cài bản TrollVNC đã vá sẽ lỗi
        lặng lẽ, mà một dòng thoáng qua ở thanh trạng thái thì rất dễ bỏ sót.
        """

        key_list = list(keys)
        failures: List[tuple] = []
        succeeded: List[str] = []

        async def run() -> None:
            async def one(key: str) -> None:
                try:
                    message = await action(self._channel(key))
                    succeeded.append(key)
                    if on_event:
                        on_event(key, message)
                except Exception as exc:
                    failures.append((key, str(exc)))
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)
            if on_done:
                on_done(describe, len(succeeded), failures)

        self._call_coro(run())

    def launch_app(self, keys: Iterable[str], bundle_id: str,
                   on_event=None, on_done=None) -> None:
        async def action(channel):
            await channel.launch(bundle_id)
            return f"đã mở {bundle_id}"

        self._bulk_app_action(keys, f"Mở {bundle_id}", action, on_event, on_done)

    def terminate_app(self, keys: Iterable[str], bundle_id: str,
                      on_event=None, on_done=None) -> None:
        async def action(channel):
            closed = await channel.terminate(bundle_id)
            return f"đã đóng {bundle_id}" if closed else f"{bundle_id} vốn không chạy"

        self._bulk_app_action(keys, f"Đóng {bundle_id}", action, on_event, on_done)

    # --------------------------------------------------------------- SSH

    def _ssh(self, key: str):
        from ..ssh_channel import SshChannel

        session = self._sessions.get(key)
        host = session.spec.host if session else key.partition(":")[0]
        return SshChannel(host, self.settings.ssh_port, self.settings.ssh_user,
                          self.settings.ssh_password)

    def run_ssh(self, keys: Iterable[str], command: str, on_result=None,
                on_done=None) -> None:
        """Chạy một lệnh shell trên nhiều máy đã jailbreak.

        on_result(key, CommandResult|None, error|None) cho từng máy,
        on_done(mô tả, số máy thành công, danh sách lỗi) khi xong hết.
        """

        key_list = list(keys)
        failures: List[tuple] = []
        succeeded: List[str] = []

        async def run() -> None:
            async def one(key: str) -> None:
                try:
                    result = await self._ssh(key).run(command)
                    if result.ok:
                        succeeded.append(key)
                    else:
                        failures.append((key, f"mã {result.exit_code}: {result.output}"))
                    if on_result:
                        on_result(key, result, None)
                except Exception as exc:
                    failures.append((key, str(exc)))
                    if on_result:
                        on_result(key, None, str(exc))

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)
            if on_done:
                on_done(command, len(succeeded), failures)

        self._call_coro(run())

    def ssh_available(self, keys: Iterable[str], on_done=None) -> None:
        """Máy nào đang có SSH — tức máy nào còn jailbreak sau lần reboot cuối."""

        key_list = list(keys)

        async def run() -> None:
            async def one(key: str) -> tuple:
                return key, await self._ssh(key).is_available()

            pairs = await asyncio.gather(*(one(k) for k in key_list),
                                         return_exceptions=True)
            alive = [k for item in pairs if isinstance(item, tuple)
                     for k, ok in [item] if ok]
            if on_done:
                on_done(alive, [k for k in key_list if k not in alive])

        self._call_coro(run())

    def push_file(self, keys: Iterable[str], local: Path | str, remote: str,
                  on_event=None) -> None:
        """Đẩy một file lên nhiều máy."""

        key_list = list(keys)

        async def run() -> None:
            async def one(key: str) -> None:
                try:
                    written = await self._channel(key).put_file(local, remote)
                    if on_event:
                        on_event(key, f"đã ghi {written} byte vào {remote}")
                except Exception as exc:
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)

        self._call_coro(run())

    def install_ipa(self, keys: Iterable[str], ipa: Path | str,
                    on_event=None, on_done=None, serve_seconds: float = 300) -> None:
        """Cài .ipa lên nhiều máy: phục vụ file từ PC rồi nhờ TrollStore tải về.

        Web server sống thêm ``serve_seconds`` sau khi gửi lệnh, vì máy còn phải
        tải file — tắt ngay thì việc cài hỏng giữa chừng.
        """

        key_list = list(keys)
        ipa = Path(ipa)

        async def run() -> None:
            from ..fileserver import FileServer, local_ip

            server = FileServer()
            await server.start()
            name = server.add(ipa)
            url = server.url_for(name, local_ip())
            if on_event:
                on_event("", f"phục vụ {name} tại {url}")

            async def one(key: str) -> None:
                try:
                    await self._channel(key).install_ipa(url)
                    if on_event:
                        on_event(key, "đã gửi lệnh cài, chờ TrollStore trên máy")
                except Exception as exc:
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)

            deadline = time.monotonic() + serve_seconds
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                if server.hits.get(name, 0) >= len(key_list):
                    break

            downloads = server.hits.get(name, 0)
            await server.stop()
            if on_event:
                on_event("", f"{downloads}/{len(key_list)} máy đã tải xong file")
            if on_done:
                on_done()

        self._call_coro(run())

    def open_url(self, keys: Iterable[str], url: str, on_event=None) -> None:
        key_list = list(keys)

        async def run() -> None:
            async def one(key: str) -> None:
                try:
                    await self._channel(key).open_url(url)
                    if on_event:
                        on_event(key, f"đã mở {url}")
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
                # Máy ngoài khung nhìn đang ngủ vẫn phải chạy được kịch bản,
                # nếu không thì chọn 250 máy rồi chạy sẽ bỏ sót phần lớn.
                session = await self._ensure_awake(key)
                if session is None or session.state is not State.ONLINE:
                    if on_event:
                        on_event(key, "bỏ qua: chưa kết nối")
                    return
                self._idle_since.pop(key, None)
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
