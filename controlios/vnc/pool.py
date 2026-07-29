"""Runs every VncSession on one asyncio loop in a background thread.

All public methods are safe to call from the Qt thread; they marshal onto the
loop with call_soon_threadsafe / run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Dict, Iterable, Mapping, Optional, Sequence

from ..config import DeviceSpec, Settings
from .session import FrameSink, State, StatusSink, Tier, VncSession

log = logging.getLogger(__name__)


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

    def type_text(self, keys: Iterable[str], text: str) -> None:
        self._call(lambda: [self._with(k, lambda s: s.type_text(text)) for k in list(keys)])

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
