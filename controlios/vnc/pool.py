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
        self._offscreen_sleeping: Dict[str, asyncio.Task] = {}
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

    def set_live_quality(self, key: str, fps: float, long_edge: int) -> None:
        self._call(lambda: self._with(
            key, lambda session: session.set_live_quality(fps, long_edge)))

    def reconnect_now(self, keys: Optional[Iterable[str]] = None) -> None:
        """Thử nối lại NGAY các máy đang rớt (bỏ qua chờ backoff). keys=None ->
        tất cả. Dùng sau khi cài đè + mở lại app trên iOS để khỏi đợi backoff."""
        self._call(self._reconnect_now, keys)

    def _reconnect_now(self, keys: Optional[Iterable[str]]) -> None:
        target = set(keys) if keys is not None else None
        for key, session in self._sessions.items():
            if target is not None and key not in target:
                continue
            if session.is_running():
                session.reconnect_now()   # đang backoff -> bừng dậy nối lại ngay
            else:
                session.start()           # đang ngủ hẳn -> khởi động lại

    def _set_tiers(self, tiers: Mapping[str, Tier]) -> None:
        now = time.monotonic()
        for key, session in self._sessions.items():
            tier = tiers.get(key, Tier.IDLE)
            session.set_tier(tier)
            if tier > Tier.IDLE:
                self._idle_since.pop(key, None)
                if key not in self._offscreen_sleeping and not session.is_running():
                    session.start()          # đánh thức máy đang ngủ
            else:
                self._idle_since.setdefault(key, now)
                if (self.settings.disconnect_offscreen
                        and self.settings.idle_disconnect_after != 0
                        and session.is_running()
                        and key not in self._offscreen_sleeping):

                    async def sleep_offscreen(k=key, s=session):
                        try:
                            await s.sleep()
                        finally:
                            if self._offscreen_sleeping.get(k) is asyncio.current_task():
                                self._offscreen_sleeping.pop(k, None)
                            if s.tier > Tier.IDLE and not s.is_running():
                                s.start()
                    self._offscreen_sleeping[key] = asyncio.create_task(sleep_offscreen())

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

    def wake_if_locked(self, key: str, on_event=None) -> None:
        """Kiểm tra sau khi VNC online và bấm Home nếu iOS đang khóa/tắt."""

        async def run() -> None:
            try:
                pressed = await self._channel(key).wake_if_locked()
                if on_event:
                    on_event(key, "đã bấm Home vì màn hình đang khóa" if pressed
                             else "màn hình không khóa")
            except Exception as exc:
                if on_event:
                    on_event(key, f"không kiểm tra được khóa màn hình: {exc}")

        self._call_coro(run())

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
        spec = session.spec if session else None
        host = spec.host if spec else key.partition(":")[0]
        # Cổng control riêng của máy (chế độ USB) nếu có, không thì cổng chung.
        port = (spec.control_port if spec and spec.control_port
                else self.settings.control_port)
        # Qua USB, control socket là loopback trên máy -> gửi lệnh không kèm auth.
        loopback = bool(spec and spec.is_usb)
        return ControlChannel(host, port, self.settings.control_token,
                              loopback=loopback)

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

    def wipe_app(self, keys: Iterable[str], bundle_id: str,
                 on_event=None, on_done=None) -> None:
        """Xoá dữ liệu app trên nhiều máy như vừa cài lại (giữ container).

        Đóng app trước để nó ghi/nhả file, rồi mới xoá. KHÔNG đụng keychain —
        token/khoá cũ vẫn còn (xem docs).
        """

        async def action(channel):
            await channel.terminate(bundle_id)
            await channel.wipe_app(bundle_id)
            return f"đã xoá dữ liệu {bundle_id}"

        self._bulk_app_action(keys, f"Xoá dữ liệu {bundle_id}", action,
                              on_event, on_done)

    def snapshot_app(self, keys: Iterable[str], bundle_id: str, name: str = "",
                     on_event=None, on_done=None) -> None:
        """Lưu một bản snapshot dữ liệu app (ngay trên mỗi máy).

        ``name`` trống thì mỗi máy tự đặt tên theo thời gian. Muốn khôi phục hàng
        loạt về sau thì nên đặt tên chung để mọi máy có cùng một tên.
        """

        async def action(channel):
            await channel.terminate(bundle_id)   # đóng để chụp trạng thái nhất quán
            saved = await channel.snapshot_app(bundle_id, name)
            return f"đã lưu snapshot “{saved}”"

        label = f"Snapshot {bundle_id}" + (f" ({name})" if name else "")
        self._bulk_app_action(keys, label, action, on_event, on_done)

    def restore_app(self, keys: Iterable[str], bundle_id: str, name: str,
                    on_event=None, on_done=None) -> None:
        """Khôi phục dữ liệu app về bản snapshot ``name``, trên nhiều máy."""

        async def action(channel):
            await channel.terminate(bundle_id)
            await channel.restore_app(bundle_id, name)
            return f"đã khôi phục về “{name}”"

        self._bulk_app_action(keys, f"Khôi phục {bundle_id} ({name})", action,
                              on_event, on_done)

    def delete_snapshot(self, keys: Iterable[str], bundle_id: str, name: str,
                        on_event=None, on_done=None) -> None:
        """Xoá một bản snapshot trên nhiều máy."""

        async def action(channel):
            await channel.delete_snapshot(bundle_id, name)
            return f"đã xoá snapshot “{name}”"

        self._bulk_app_action(keys, f"Xoá snapshot {bundle_id} ({name})", action,
                              on_event, on_done)

    def clear_snapshots(self, keys: Iterable[str], bundle_id: str,
                        on_event=None, on_done=None) -> None:
        """Xoá tất cả snapshot của một app trên nhiều máy."""

        async def action(channel):
            await channel.clear_snapshots(bundle_id)
            return "đã xoá tất cả snapshot"

        self._bulk_app_action(keys, f"Xoá tất cả snapshot {bundle_id}", action,
                              on_event, on_done)

    def list_snapshots(self, key: str, bundle_id: str, on_done) -> None:
        """Liệt kê snapshot của một app trên MỘT máy.

        on_done(key, danh sách Snapshot, error) — chạy trên luồng mạng.
        """

        async def run() -> None:
            try:
                snaps = await self._channel(key).list_snapshots(bundle_id)
                on_done(key, snaps, None)
            except Exception as exc:
                on_done(key, [], str(exc))

        self._call_coro(run())

    def export_snapshot(self, key: str, bundle_id: str, name: str,
                        destination: Path | str, on_event=None, on_done=None) -> None:
        """Tải một snapshot từ máy về PC qua SFTP/SSH."""

        destination = Path(destination)

        async def run() -> None:
            session = self._sessions.get(key)
            label = session.spec.name if session and session.spec.name else key
            remote = f"/var/mobile/controlios-snap/{bundle_id}/{name}"
            local = destination / _slug(label) / _slug(bundle_id) / _slug(name)
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                written = await self._channel(key).download_tree(remote, local)
                if on_event:
                    on_event(key, f"đã xuất {written} byte snapshot ra {local}")
                if on_done:
                    on_done(f"Xuất snapshot {bundle_id} ({name})", 1, [])
            except Exception as exc:
                if on_event:
                    on_event(key, f"LỖI {exc}")
                if on_done:
                    on_done(f"Xuất snapshot {bundle_id} ({name})", 0,
                            [(key, str(exc))])

        self._call_coro(run())

    def backup_app_to_pc(self, keys: Iterable[str], bundle_id: str, name: str,
                         destination: Path | str, on_event=None, on_done=None) -> None:
        """Tạo snapshot rồi tải về PC cho toàn bộ máy được chọn, không cần SSH."""

        key_list = list(keys)
        destination = Path(destination)
        succeeded: List[str] = []
        failures: List[tuple] = []

        async def run() -> None:
            transfer_slots = asyncio.Semaphore(4)

            async def one(key: str) -> None:
                session = self._sessions.get(key)
                label = session.spec.name if session and session.spec.name else key
                device_dir = destination / f"{_slug(label)}_{_slug(key)}"
                local = device_dir / _slug(bundle_id) / _slug(name)
                try:
                    async with transfer_slots:
                        channel = self._channel(key)
                        await channel.terminate(bundle_id)
                        saved = await channel.snapshot_app(bundle_id, name)
                        remote = f"/var/mobile/controlios-snap/{bundle_id}/{saved}"
                        written = await channel.download_tree(remote, local)
                    succeeded.append(key)
                    if on_event:
                        on_event(key, f"đã sao lưu {written} byte ra {local}")
                except Exception as exc:
                    failures.append((key, str(exc)))
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(key) for key in key_list), return_exceptions=True)
            if on_done:
                on_done(f"Sao lưu {bundle_id} ra PC ({name})",
                        len(succeeded), failures)

        self._call_coro(run())

    def respring(self, keys: Iterable[str], on_event=None, on_done=None) -> None:
        """Khởi động lại SpringBoard trên nhiều máy (không mất jailbreak)."""

        async def action(channel):
            await channel.respring()
            return "đã respring"

        self._bulk_app_action(keys, "Respring", action, on_event, on_done)

    def assistive_touch(self, keys: Iterable[str], state: str,
                        on_event=None, on_done=None) -> None:
        """Bật/tắt AssistiveTouch iOS trên nhiều máy. state = on|off|toggle."""

        async def action(channel):
            await channel.set_assistive_touch(state)
            return f"AssistiveTouch {state}"

        self._bulk_app_action(keys, f"AssistiveTouch {state}", action, on_event, on_done)

    def reboot(self, keys: Iterable[str], on_event=None, on_done=None) -> None:
        async def action(channel):
            await channel.reboot()
            return "đã gửi lệnh reboot"
        self._bulk_app_action(keys, "Reboot thiết bị", action, on_event, on_done)

    def shutdown(self, keys: Iterable[str], on_event=None, on_done=None) -> None:
        async def action(channel):
            await channel.shutdown()
            return "đã gửi lệnh tắt máy"
        self._bulk_app_action(keys, "Tắt máy", action, on_event, on_done)

    def ensure_keeper(self, keys: Iterable[str],
                      on_event=None, on_done=None) -> None:
        """Kiểm tra ControlIOSKeeper trên nhiều máy, bật lại máy nào đang chết.

        Đây là vòng canh NGOÀI cùng của farm: keeperd canh ControlIOS, còn PC
        canh keeperd. Máy nào không trả lời control socket thì báo lỗi bình
        thường như các tác vụ bulk khác."""

        async def action(channel):
            started, note = await channel.ensure_keeper()
            return ("đã bật lại Keeper: " if started else "") + note

        self._bulk_app_action(keys, "Kiểm tra Keeper", action, on_event, on_done)

    def push_and_run_autoscript(self, keys: Iterable[str], script: str,
                                on_event=None, on_done=None) -> None:
        """Đẩy kịch bản auto-click JS xuống nhiều máy RỒI chạy ngay (farm)."""

        async def action(channel):
            await channel.push_autoscript(script)
            await channel.autoclick_start()
            return "đã đẩy + chạy"

        self._bulk_app_action(keys, "Auto-click: đẩy & chạy", action, on_event, on_done)

    def push_autoscript(self, keys: Iterable[str], script: str,
                        on_event=None, on_done=None) -> None:
        """Chỉ đẩy kịch bản (không chạy)."""

        async def action(channel):
            await channel.push_autoscript(script)
            return "đã đẩy kịch bản"

        self._bulk_app_action(keys, "Auto-click: đẩy", action, on_event, on_done)

    def push_prelude(self, keys: Iterable[str], js: str,
                     on_event=None, on_done=None) -> None:
        """Đẩy THƯ VIỆN HÀM (JS) xuống nhiều máy — thêm hàm mới không cần cài lại."""

        async def action(channel):
            await channel.push_prelude(js)
            return "đã đẩy thư viện hàm"

        self._bulk_app_action(keys, "Auto-click: đẩy thư viện hàm", action, on_event, on_done)

    def autoclick_stop(self, keys: Iterable[str], on_event=None, on_done=None) -> None:
        """Dừng auto-click trên nhiều máy."""

        async def action(channel):
            await channel.autoclick_stop()
            return "đã dừng"

        self._bulk_app_action(keys, "Auto-click: dừng", action, on_event, on_done)

    def autolog(self, key: str, on_done) -> None:
        """Lấy (đang chạy?, nhật ký) auto-click của MỘT máy. on_done(key, running, log)."""

        async def run() -> None:
            try:
                running, log = await self._channel(key).get_autolog()
                on_done(key, running, log)
            except Exception as exc:
                on_done(key, False, f"(không lấy được nhật ký: {exc})")

        self._call_coro(run())

    def clear_autolog(self, key: str, on_done=None) -> None:
        """Xoá nhật ký auto-click trên MỘT máy."""

        async def run() -> None:
            try:
                await self._channel(key).clear_autolog()
                if on_done:
                    on_done(key, None)
            except Exception as exc:
                if on_done:
                    on_done(key, str(exc))

        self._call_coro(run())

    def read_color(self, key: str, rx: float, ry: float, on_done) -> None:
        """Đọc MÀU THẬT tại điểm tỉ lệ (rx, ry) trên MỘT máy (daemon lấy pixel
        gốc). on_done(key, hex_or_None, err_or_None). Chặn ~3.5s để không treo."""

        async def run() -> None:
            try:
                hexv = await asyncio.wait_for(
                    self._channel(key).get_color(rx, ry), timeout=3.5)
                on_done(key, hexv, None)
            except Exception as exc:
                on_done(key, None, str(exc))

        self._call_coro(run())

    def set_smoothness(self, keys: Iterable[str], inflight: int, defer: float,
                       orientation_sync: bool, on_event=None, on_done=None) -> None:
        """Áp tham số ĐỘ MƯỢT (Q/defer/xoay) lên nhiều máy — không resize nên
        KHÔNG nối lại, không chớp đen."""

        async def action(channel):
            await channel.set_smoothness(inflight, defer, orientation_sync)
            return f"Q={inflight} defer={defer:.3f} xoay={'on' if orientation_sync else 'off'}"

        self._bulk_app_action(keys, "Độ mượt", action, on_event, on_done)

    def set_scale(self, keys: Iterable[str], factor: float,
                  on_event=None, on_done=None) -> None:
        """Đổi hệ số scale khung hình trên nhiều máy (giảm tải máy đời cũ).

        Đổi scale làm framebuffer đổi kích thước; client này không đăng ký
        DesktopSize nên phải **nối lại phiên** để lấy cỡ mới, tránh cảnh hai màn
        hình lồng nhau. Sau khi đặt scale, chờ máy áp resize rồi mới nối lại.
        """

        key_list = list(keys)
        failures: List[tuple] = []
        succeeded: List[str] = []

        async def run() -> None:
            async def one(key: str) -> None:
                try:
                    await self._channel(key).set_scale(factor)
                    session = self._sessions.get(key)
                    if session:
                        # ĐẶT resync NGAY (không await ở giữa) để cờ được bật TRƯỚC
                        # khi pacer kịp gửi một frame ở cỡ mới — nếu để pacer gửi
                        # trước, scale TO LÊN sẽ làm client lỗi (frame vượt buffer)
                        # và nối lại theo đường backoff chậm ~10s thay vì nhanh.
                        session.request_resync()
                    if on_event:
                        on_event(key, f"đã đặt scale {factor:.2f}, đang nối lại…")
                    if session:
                        ok = await self._wait_reconnect(session, timeout=15.0)
                        if on_event:
                            on_event(key, "đã nối lại ✓" if ok
                                     else "nối lại chậm — thử lại nếu hình chưa đúng")
                    succeeded.append(key)
                except Exception as exc:
                    failures.append((key, str(exc)))
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)
            if on_done:
                on_done(f"Scale {factor:.2f}", len(succeeded), failures)

        self._call_coro(run())

    async def _wait_reconnect(self, session, timeout: float) -> bool:
        """Chờ một phiên rời ONLINE rồi ONLINE trở lại (sau khi resync)."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Chờ rời ONLINE (bắt đầu nối lại).
        while session.state is State.ONLINE and loop.time() < deadline:
            await asyncio.sleep(0.1)
        # Rồi chờ ONLINE trở lại.
        while session.state is not State.ONLINE and loop.time() < deadline:
            await asyncio.sleep(0.1)
        return session.state is State.ONLINE

    def set_clipboard(self, keys: Iterable[str], text: str,
                      on_event=None, on_done=None) -> None:
        """Đặt clipboard (UTF-8) cho nhiều máy — dán khối chữ dài hàng loạt."""

        async def action(channel):
            written = await channel.set_clipboard(text)
            return f"đã đặt clipboard ({written} byte)"

        self._bulk_app_action(keys, "Đặt clipboard", action, on_event, on_done)

    def get_clipboard(self, key: str, on_done) -> None:
        """Đọc clipboard của MỘT máy (UTF-8). on_done(key, text_or_None, err)."""

        async def run() -> None:
            try:
                text = await asyncio.wait_for(
                    self._channel(key).get_clipboard(), timeout=4.0)
                on_done(key, text, None)
            except Exception as exc:
                on_done(key, None, str(exc))

        self._call_coro(run())

    def push_photo(self, keys: Iterable[str], local: Path | str,
                   on_event=None, on_done=None) -> None:
        """Đẩy ảnh/video rồi nạp vào Thư viện Ảnh trên nhiều máy.

        Video chưa đúng chuẩn iOS được **tự re-encode một lần** (ffmpeg) trước
        khi phát cho cả mẻ, nên bạn chỉ cần đưa file gốc.
        """

        from .. import media

        key_list = list(keys)
        source = Path(local)
        failures: List[tuple] = []
        succeeded: List[str] = []

        async def run() -> None:
            # Chuẩn hoá MỘT lần cho cả mẻ (transcode nặng nên chạy ở thread riêng).
            ready = source
            try:
                ready = await asyncio.to_thread(media.ensure_ios_media, source)
            except Exception as exc:
                if on_event:
                    on_event("*", f"Không chuẩn hoá được, đẩy nguyên bản: {exc}")
            if ready != source and on_event:
                on_event("*", f"Đã chuẩn hoá {source.name} → {ready.name} cho iOS")

            async def one(key: str) -> None:
                try:
                    await self._channel(key).push_photo(ready, normalize=False)
                    succeeded.append(key)
                    if on_event:
                        on_event(key, f"đã nạp {ready.name} vào Thư viện")
                except Exception as exc:
                    failures.append((key, str(exc)))
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)
            if on_done:
                on_done(f"Nạp {source.name}", len(succeeded), failures)

        self._call_coro(run())

    # --------------------------------------------------------------- SSH

    def _ssh(self, key: str):
        from ..ssh_channel import SshChannel

        session = self._sessions.get(key)
        spec = session.spec if session else None
        host = spec.host if spec else key.partition(":")[0]
        port = spec.ssh_port if spec and spec.ssh_port else self.settings.ssh_port
        return SshChannel(host, port, self.settings.ssh_user,
                          self.settings.ssh_password,
                          key_path=self.settings.ssh_key_path)

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

    def export_path(self, keys: Iterable[str], remote: str, destination: Path | str,
                    on_event=None, on_done=None) -> None:
        """Tải file hoặc cả thư mục từ nhiều máy, tách thư mục theo từng máy."""

        key_list = list(keys)
        destination = Path(destination)
        failures: List[tuple] = []
        succeeded: List[str] = []

        async def run() -> None:
            async def one(key: str) -> None:
                session = self._sessions.get(key)
                spec = session.spec if session else None
                label = (spec.name if spec and spec.name else key)
                device_dir = destination / _slug(label)
                leaf = Path(remote.rstrip("/")).name or "ios-root"
                local = device_dir / leaf
                try:
                    device_dir.mkdir(parents=True, exist_ok=True)
                    await self._ssh(key).download(remote, local, recursive=True)
                    succeeded.append(key)
                    if on_event:
                        on_event(key, f"đã xuất {remote} → {local}")
                except Exception as exc:
                    failures.append((key, str(exc)))
                    if on_event:
                        on_event(key, f"LỖI {exc}")

            await asyncio.gather(*(one(k) for k in key_list), return_exceptions=True)
            if on_done:
                on_done(f"Xuất {remote}", len(succeeded), failures)

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

    def online_keys(self) -> List[str]:
        """Máy đang ONLINE — để phát lệnh control cho toàn bộ máy đang xem."""
        return [key for key, session in list(self._sessions.items())
                if session.state is State.ONLINE]

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
