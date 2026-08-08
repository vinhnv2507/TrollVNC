"""A single TrollVNC connection, with a tier that decides how much it costs.

The scaling trick for hundreds of phones lives here: RFB only sends pixels the
client asks for. A session in IDLE tier keeps its TCP connection and its input
channel, but issues no FramebufferUpdateRequest at all, so it consumes ~no
bandwidth and ~no CPU. Only tiles the user can actually see are promoted to
GRID (a slow trickle of frames) or LIVE (interactive rate).
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

import asyncvnc
import numpy as np

from ..config import DeviceSpec, Settings

log = logging.getLogger(__name__)

# Keysym XF86 mà TrollVNC dịch thành phím cứng của iPhone. Không có trong bảng
# tên của asyncvnc nên phải gửi bằng mã số.
MEDIA_KEYSYMS = {
    "brightness_up": 0x1008FF02,
    "brightness_down": 0x1008FF03,
    "volume_up": 0x1008FF13,
    "volume_down": 0x1008FF11,
    "mute": 0x1008FF12,
    "play": 0x1008FF14,
    "prev": 0x1008FF3E,
    "next": 0x1008FF97,
}

# iOS chia độ sáng thành 16 nấc, nên bấy nhiêu lần là chạm đáy hoặc chạm đỉnh.
BRIGHTNESS_STEPS = 16


class Tier(enum.IntEnum):
    OFF = 0     # disconnected on purpose
    IDLE = 1    # connected, no pixels requested
    GRID = 2    # thumbnail refresh at Settings.grid_fps
    LIVE = 3    # full resolution at Settings.live_fps


class State(enum.Enum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ONLINE = "online"
    ERROR = "error"
    # Chủ động ngắt để máy ngừng chụp hình — khác hẳn mất kết nối ngoài ý muốn.
    DORMANT = "dormant"


# Cách sắp xếp byte của khung hình, để phía giao diện dựng QImage cho đúng.
# Bốn kênh đi thẳng từ máy về nhanh hơn hẳn: cắt lấy 3 kênh là đọc nhảy cách,
# còn 4 kênh liền mạch chỉ là một lần chép thẳng (đo được nhanh hơn 4,6 lần).
PIXEL_RGB888 = "rgb888"      # 3 byte mỗi điểm ảnh, dùng cho ảnh đã thu nhỏ
PIXEL_BGRA32 = "bgra32"      # 4 byte, khớp QImage.Format_RGB32
PIXEL_RGBX32 = "rgbx32"      # 4 byte, khớp QImage.Format_RGBX8888

# mode của asyncvnc -> cách sắp xếp byte tương ứng
_DIRECT_MODES = {"bgra": PIXEL_BGRA32, "rgba": PIXEL_RGBX32}


@dataclass
class Frame:
    """Một khung hình đã sẵn sàng để bọc vào QImage."""

    key: str
    width: int
    height: int
    data: bytes
    full_width: int      # framebuffer size, for input coordinate mapping
    full_height: int
    pixel_format: str = PIXEL_RGB888

    @property
    def bytes_per_line(self) -> int:
        return self.width * (3 if self.pixel_format == PIXEL_RGB888 else 4)


FrameSink = Callable[[Frame], None]
StatusSink = Callable[[str, State, str], None]   # key, state, detail


def _downscale_rgb(rgba: np.ndarray, long_edge: int) -> tuple[np.ndarray, int, int]:
    """Nearest-neighbour downscale via strides — cheap enough to run 250x.

    Bước phải làm tròn **lên**: 1338/900 = 1,49 mà làm tròn xuống thành 1 thì
    hoá ra không thu nhỏ gì cả, và giới hạn bị bỏ qua trong im lặng. Đổi lại,
    vì bước là số nguyên nên kết quả có thể nhỏ hơn giới hạn khá nhiều.
    """

    height, width = rgba.shape[:2]
    limit = max(1, long_edge)
    step = max(1, -(-max(width, height) // limit))      # chia làm tròn lên
    small = rgba[::step, ::step, :3]
    return np.ascontiguousarray(small), small.shape[1], small.shape[0]


class VncSession:
    """Owns one connection. Every public method is safe to call from the
    asyncio loop thread only; the pool provides thread-safe wrappers."""

    def __init__(
        self,
        spec: DeviceSpec,
        settings: Settings,
        connect_sem: asyncio.Semaphore,
        on_frame: FrameSink,
        on_status: StatusSink,
    ) -> None:
        self.spec = spec
        self.settings = settings
        self._sem = connect_sem
        self._on_frame = on_frame
        self._on_status = on_status

        self.state = State.OFFLINE
        self.tier = Tier.IDLE
        self.last_frame_at = 0.0
        self.frame_count = 0

        self._client: Optional[asyncvnc.Client] = None
        self._capture_waiters: list[asyncio.Future] = []
        # Ép frame đầu sau khi đổi tier là full (non-incremental) để hiện ngay,
        # không phải chờ màn hình đổi mới có pixel.
        self._force_full = False
        # Bừng dậy giữa chừng một request đang chờ khi đổi tier (mở khung lớn):
        # incremental request trên màn hình tĩnh có thể treo tới stall_timeout.
        self._promote = asyncio.Event()
        # Yêu cầu nối lại phiên (framebuffer đổi kích thước, ví dụ đổi scale) —
        # client này không đăng ký DesktopSize nên phải bắt tay lại để lấy cỡ mới.
        self._resync = asyncio.Event()
        self._tier_changed = asyncio.Event()
        self._frame_ready = asyncio.Event()
        self._stop = asyncio.Event()
        # Ngắt chờ backoff để NỐI LẠI NGAY (sau khi mở lại app trên iOS chẳng hạn).
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # ---------------------------------------------------------------- control

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"vnc:{self.spec.key}")

    async def stop(self) -> None:
        self._stop.set()
        self._tier_changed.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._resolve_captures(ConnectionError(f"{self.spec.key} stopped"))
        self._set_state(State.OFFLINE, "stopped")

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def sleep(self) -> None:
        """Ngắt hẳn kết nối để máy ngừng chụp hình.

        TrollVNC chỉ chạy ScreenCapturer khi còn client nối vào, nên rời đi là
        cách duy nhất để trả CPU và bộ nhớ lại cho iPhone. Tier IDLE chỉ tiết
        kiệm băng thông và CPU phía PC, máy vẫn phải render.
        """

        await self.stop()
        self._set_state(State.DORMANT, "tạm ngắt cho máy nghỉ")

    def request_resync(self) -> None:
        """Nối lại phiên: dùng khi framebuffer đổi kích thước (đổi scale/xoay máy)."""
        self._resync.set()

    def reconnect_now(self) -> None:
        """Bỏ qua chờ backoff, thử nối lại ngay (sau khi mở lại app trên máy)."""
        self._wake.set()

    def set_tier(self, tier: Tier) -> None:
        if tier == self.tier:
            return
        self.tier = tier
        # Mở khung lớn (GRID/IDLE -> LIVE) trên màn hình tĩnh sẽ đen tới khi màn
        # hình đổi nếu xin incremental. Ép frame kế là full để hiện tức thì, và
        # ngắt request đang chờ dở để không phải đợi hết stall_timeout.
        self._force_full = True
        self._promote.set()
        self._tier_changed.set()

    # ---------------------------------------------------------------- capture

    def request_capture(self) -> asyncio.Future:
        """Ask for one full-resolution frame, whatever the current tier.

        Returns a future resolving to a :class:`Frame`. The pacer serves it on
        its next cycle, so the capture shares the one connection instead of
        racing it. Several callers asking at once share a single round trip.
        """

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        if self.state is not State.ONLINE:
            future.set_exception(ConnectionError(f"{self.spec.key} is {self.state.value}"))
            return future
        self._capture_waiters.append(future)
        self._tier_changed.set()   # wake the pacer if this session is idle
        return future

    def _resolve_captures(self, result) -> None:
        waiters, self._capture_waiters = self._capture_waiters, []
        for future in waiters:
            if future.done():
                continue
            if isinstance(result, BaseException):
                future.set_exception(result)
            else:
                future.set_result(result)

    async def _serve_capture(self, client: asyncvnc.Client) -> None:
        try:
            await self._request(client, incremental=False)
        except Exception as exc:
            self._resolve_captures(exc)
            raise
        rgb = np.ascontiguousarray(client.video.as_rgba()[:, :, :3])
        width, height = client.video.width, client.video.height
        self._resolve_captures(
            Frame(key=self.spec.key, width=width, height=height,
                  data=rgb.tobytes(), full_width=width, full_height=height)
        )

    # ------------------------------------------------------------------ input

    def tap(self, x: int, y: int, button: int = 0) -> None:
        if not self._client:
            return
        self._client.mouse.move(int(x), int(y))
        self._client.mouse.click(button)

    def mouse_down(self, x: int, y: int, button: int = 0) -> None:
        if not self._client:
            return
        self._client.mouse.move(int(x), int(y))
        self._client.mouse.buttons |= 1 << button
        self._client.mouse._write()

    def mouse_move(self, x: int, y: int) -> None:
        if self._client:
            self._client.mouse.move(int(x), int(y))

    def mouse_up(self, x: int, y: int, button: int = 0) -> None:
        if not self._client:
            return
        self._client.mouse.move(int(x), int(y))
        self._client.mouse.buttons &= ~(1 << button)
        self._client.mouse._write()

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.25,
                    steps: int = 12, hold: float = 0.0) -> None:
        """Vuốt từ (x1,y1) tới (x2,y2), rồi *giữ* nguyên `hold` giây trước khi nhả.

        Phần giữ là bắt buộc với các cử chỉ như mở App Switcher trên iOS: vuốt
        lên rồi nhả ngay chỉ về màn hình chính.
        """

        if not self._client:
            return
        self.mouse_down(x1, y1)
        for i in range(1, steps + 1):
            t = i / steps
            self.mouse_move(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
            await asyncio.sleep(duration / steps)
        if hold > 0:
            await asyncio.sleep(hold)
        self.mouse_up(x2, y2)

    def scroll(self, x: int, y: int, dx: int = 0, dy: int = 0) -> None:
        """Lăn chuột tại (x,y). dy > 0 là lăn lên, dx > 0 là lăn sang phải.

        RFB không có sự kiện lăn riêng: nó là các nút 4..7 nhấn-rồi-nhả.
        """

        if not self._client:
            return
        # Cuộn "thuận iOS": lăn bánh xe lên phải làm nội dung dịch như vuốt trên
        # iPhone. Không đảo thì cảm giác ngược chiều cuộn của iOS.
        if getattr(self.settings, "natural_scroll", True):
            dy, dx = -dy, -dx
        mouse = self._client.mouse
        mouse.move(int(x), int(y))
        for _ in range(abs(int(dy))):
            mouse.click(3 if dy > 0 else 4)
        for _ in range(abs(int(dx))):
            mouse.click(6 if dx > 0 else 5)

    def type_text(self, text: str) -> str:
        """Gõ chuỗi. Trả về các ký tự **không** gửi được (không có keysym).

        Tiếng Việt đi qua được nhờ keysym Unicode (``ạ`` = 0x1001ea1), nhưng
        emoji thì không. Trước đây một ký tự lạ làm cả kịch bản của máy đó chết
        giữa chừng; giờ nó bị bỏ qua và báo lại cho người dùng.
        """

        if not self._client:
            return text
        supported = []
        skipped = []
        for char in text:
            (supported if char in asyncvnc.key_codes else skipped).append(char)
        if supported:
            self._client.keyboard.write("".join(supported))
        return "".join(skipped)

    def press_keysym(self, keysym: int, repeat: int = 1) -> None:
        """Gửi thẳng một keysym theo mã số.

        Cần cho các phím XF86 (độ sáng, âm lượng): `asyncvnc` không có tên cho
        chúng nên không gọi qua `press_keys` được.
        """

        if not self._client:
            return
        data = int(keysym).to_bytes(4, "big")
        writer = self._client.writer
        for _ in range(max(1, int(repeat))):
            writer.write(b"\x04\x01\x00\x00" + data)   # nhấn
            writer.write(b"\x04\x00\x00\x00" + data)   # nhả

    def media_key(self, name: str, repeat: int = 1) -> None:
        """Phím đa phương tiện / độ sáng, theo tên trong :data:`MEDIA_KEYSYMS`."""

        keysym = MEDIA_KEYSYMS.get(name)
        if keysym is None:
            log.warning("%s: không biết phím %r", self.spec.key, name)
            return
        self.press_keysym(keysym, repeat)

    def press_keys(self, *keys: str) -> None:
        """Nhấn tổ hợp: giữ tất cả rồi nhả theo thứ tự ngược, ví dụ Ctrl+C."""

        if not self._client:
            return
        unknown = [k for k in keys if k not in asyncvnc.key_codes]
        if unknown:
            log.warning("%s: không có keysym cho %s", self.spec.key, unknown)
            return
        self._client.keyboard.press(*keys)

    # ------------------------------------------------------------------- loop

    def _set_state(self, state: State, detail: str = "") -> None:
        self.state = state
        try:
            self._on_status(self.spec.key, state, detail)
        except Exception:  # a broken UI sink must not kill the session
            log.exception("status sink failed for %s", self.spec.key)

    async def _run(self) -> None:
        delay = self.settings.reconnect_delay
        while not self._stop.is_set():
            try:
                self._set_state(State.CONNECTING)
                async with self._sem:
                    cm = asyncvnc.connect(
                        self.spec.host, self.spec.port, password=self.spec.password
                    )
                    client = await asyncio.wait_for(cm.__aenter__(), timeout=15)
                try:
                    self._client = client
                    self._set_state(State.ONLINE)
                    delay = self.settings.reconnect_delay
                    await self._session(client)
                finally:
                    self._client = None
                    # Never leave a capture caller waiting on a dead session.
                    self._resolve_captures(
                        ConnectionError(f"{self.spec.key} disconnected during capture")
                    )
                    try:
                        await cm.__aexit__(None, None, None)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set_state(State.ERROR, f"{type(exc).__name__}: {exc}")

            if self._stop.is_set():
                break
            if self._resync.is_set():
                # Nối lại chủ động (đổi scale) — nhanh, không backoff.
                self._resync.clear()
                await asyncio.sleep(0.3)
                delay = self.settings.reconnect_delay
            else:
                # Chờ backoff nhưng BỪNG DẬY NGAY nếu có yêu cầu nối lại (mở lại
                # app trên máy) -> khỏi phải đợi hết chu kỳ backoff dài.
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                    self._wake.clear()
                    delay = self.settings.reconnect_delay
                except asyncio.TimeoutError:
                    delay = min(delay * 2, self.settings.reconnect_max)

        self._set_state(State.OFFLINE)

    async def _session(self, client: asyncvnc.Client) -> None:
        """Reader and pacer run concurrently; either failing ends the session."""

        reader = asyncio.create_task(self._read_loop(client))
        pacer = asyncio.create_task(self._pace_loop(client))
        # Cũng thức dậy khi có yêu cầu nối lại (đổi scale) để bắt tay lại lấy cỡ mới.
        resync = asyncio.create_task(self._resync.wait())
        tasks = {reader, pacer, resync}
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            # Also runs when _run itself is cancelled, so neither task leaks.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            if task is resync:
                continue        # yêu cầu nối lại: kết thúc phiên êm, không phải lỗi
            if not task.cancelled() and task.exception():
                raise task.exception()

    async def _read_loop(self, client: asyncvnc.Client) -> None:
        while not self._stop.is_set():
            update = await client.read()
            if update is asyncvnc.UpdateType.VIDEO:
                self.last_frame_at = time.monotonic()
                self.frame_count += 1
                self._emit(client)
                self._frame_ready.set()

    async def _pace_loop(self, client: asyncvnc.Client) -> None:
        first = True
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            if self._capture_waiters:
                first = False
                await self._serve_capture(client)
                continue

            tier = self.tier

            if tier <= Tier.IDLE:
                if first:
                    # One full frame so the tile has something to show, then
                    # drop the framebuffer to keep 250 idle sessions cheap.
                    first = False
                    await self._request(client, incremental=False)
                    continue
                client.video.data = None
                self._tier_changed.clear()
                if self.tier > Tier.IDLE or self._capture_waiters:
                    continue  # promoted, or a capture landed while we tidied up
                await self._tier_changed.wait()
                continue

            first = False
            self._promote.clear()
            started = loop.time()
            incremental = client.video.data is not None and not self._force_full
            self._force_full = False
            await self._request(client, incremental=incremental, interruptible=True)
            fps = self.settings.live_fps if tier is Tier.LIVE else self.settings.grid_fps
            # Nhịp theo thời gian thực: chỉ ngủ phần còn thiếu để chạm fps mục
            # tiêu, không cộng cả chu kỳ lên trên thời gian chờ frame. Nhờ vậy
            # đường nhanh (USB) chạy sát fps thay vì bị hãm còn phân nửa.
            remaining = (1.0 / max(fps, 0.05)) - (loop.time() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def _request(self, client: asyncvnc.Client, incremental: bool,
                       interruptible: bool = False) -> None:
        if not incremental:
            client.video.data = None
        self._frame_ready.clear()
        client.video.refresh()
        await client.drain()
        # Wait for the answer instead of pipelining requests: a phone that
        # stops painting must not accumulate an unbounded request queue.
        if not interruptible:
            try:
                await asyncio.wait_for(
                    self._frame_ready.wait(), timeout=self.settings.stall_timeout
                )
            except asyncio.TimeoutError:
                raise TimeoutError("no framebuffer update")
            return

        # Chỉ luồng pacer mới ngắt được: bừng dậy nếu vừa đổi tier (mở khung lớn),
        # vì incremental request trên màn hình tĩnh có thể treo tới stall_timeout
        # và chặn việc hiện khung lớn ngay. Chụp ảnh thì KHÔNG ngắt (cần đủ frame).
        frame_wait = asyncio.ensure_future(self._frame_ready.wait())
        promote_wait = asyncio.ensure_future(self._promote.wait())
        try:
            done, _pending = await asyncio.wait(
                {frame_wait, promote_wait},
                timeout=self.settings.stall_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (frame_wait, promote_wait):
                if not task.done():
                    task.cancel()
        if not done:
            raise TimeoutError("no framebuffer update")

    def _emit(self, client: asyncvnc.Client) -> None:
        video = client.video
        if video.data is None:
            return
        limit = self.settings.live_long_edge if self.tier is Tier.LIVE \
            else self.settings.thumb_long_edge
        direct = _DIRECT_MODES.get(video.mode)

        if self.tier is Tier.LIVE and (not limit or max(video.width, video.height) <= limit) \
                and direct:
            # Đường nhanh: đưa nguyên bộ đệm 4 kênh, Qt đọc thẳng được.
            data = np.ascontiguousarray(video.data).tobytes()
            width, height, fmt = video.width, video.height, direct
        else:
            rgba = video.as_rgba()
            if limit and max(video.width, video.height) > limit:
                small, width, height = _downscale_rgb(rgba, limit)
            else:
                small = np.ascontiguousarray(rgba[:, :, :3])
                width, height = video.width, video.height
            data, fmt = small.tobytes(), PIXEL_RGB888

        try:
            self._on_frame(
                Frame(
                    key=self.spec.key,
                    width=width,
                    height=height,
                    data=data,
                    full_width=video.width,
                    full_height=video.height,
                    pixel_format=fmt,
                )
            )
        except Exception:
            log.exception("frame sink failed for %s", self.spec.key)
