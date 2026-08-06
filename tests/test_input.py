"""Chuột và bàn phím: kiểm tra sự kiện thật sự tới server, đúng nút, đúng phím."""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncvnc                                             # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt              # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

from controlios.config import DeviceSpec, Settings          # noqa: E402
from controlios.ui.detail import DetailView                 # noqa: E402
from controlios.vnc.session import Frame, State, Tier, VncSession  # noqa: E402
from tests.fake_vnc import FakeVncServer                    # noqa: E402

app = QApplication.instance() or QApplication([])

RETURN_KEYSYM = asyncvnc.key_codes["Return"]
CTRL_KEYSYM = asyncvnc.key_codes["Ctrl"]


class SessionInputTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeVncServer()
        port = await self.server.start()
        self.session = VncSession(
            DeviceSpec(host="127.0.0.1", port=port),
            Settings(grid_fps=20.0, stall_timeout=5.0, reconnect_delay=0.2),
            asyncio.Semaphore(4),
            on_frame=lambda f: None, on_status=lambda k, s, d: None,
        )
        self.session.set_tier(Tier.GRID)
        self.session.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self.session.state is not State.ONLINE:
            await asyncio.sleep(0.05)
        self.assertIs(self.session.state, State.ONLINE)

    async def asyncTearDown(self) -> None:
        await self.session.stop()
        await self.server.stop()

    async def drain(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.05)
        return False

    async def test_scroll_up_and_down_use_distinct_buttons(self) -> None:
        # Kiểm ánh xạ nút THÔ, nên tắt đảo chiều "cuộn thuận iOS".
        self.session.settings.natural_scroll = False
        self.server.pointer_events.clear()
        self.session.scroll(100, 200, dy=2)
        self.assertTrue(await self.drain(lambda: len(self.server.pointer_events) >= 4))

        pressed = {e[0] for e in self.server.pointer_events if e[0]}
        self.assertIn(1 << 3, pressed, f"lăn lên phải là nút 4 (mask 8): {pressed}")

        self.server.pointer_events.clear()
        self.session.scroll(100, 200, dy=-1)
        self.assertTrue(await self.drain(lambda: len(self.server.pointer_events) >= 2))
        pressed = {e[0] for e in self.server.pointer_events if e[0]}
        self.assertIn(1 << 4, pressed, f"lăn xuống phải là nút 5 (mask 16): {pressed}")

    async def test_horizontal_scroll_uses_buttons_six_and_seven(self) -> None:
        self.session.settings.natural_scroll = False
        self.server.pointer_events.clear()
        self.session.scroll(50, 50, dx=1)
        self.assertTrue(await self.drain(lambda: len(self.server.pointer_events) >= 2))
        pressed = {e[0] for e in self.server.pointer_events if e[0]}
        self.assertIn(1 << 6, pressed, f"lăn phải phải là nút 7: {pressed}")

    async def test_right_and_middle_button_are_not_left(self) -> None:
        for button, mask in [(1, 1 << 1), (2, 1 << 2)]:
            self.server.pointer_events.clear()
            self.session.tap(10, 20, button)
            self.assertTrue(await self.drain(
                lambda: any(e[0] == mask for e in self.server.pointer_events)),
                f"nút {button} không tới được server: {self.server.pointer_events}")

    async def test_vietnamese_text_is_typed_not_dropped(self) -> None:
        self.server.key_events.clear()
        skipped = self.session.type_text("Xin chào bạn")
        self.assertEqual(skipped, "", "tiếng Việt phải gửi được hết")
        self.assertTrue(await self.drain(lambda: len(self.server.key_events) >= 24))

        typed = "".join(
            chr(sym - 0x01000000 if sym > 0x01000000 else sym)
            for down, sym in self.server.key_events if down == 1
        )
        self.assertEqual(typed, "Xin chào bạn")

    async def test_unsupported_characters_are_reported_not_fatal(self) -> None:
        self.server.key_events.clear()
        skipped = self.session.type_text("ok😀 rồi")

        self.assertEqual(skipped, "😀")
        self.assertTrue(await self.drain(lambda: len(self.server.key_events) >= 12))
        typed = "".join(
            chr(sym - 0x01000000 if sym > 0x01000000 else sym)
            for down, sym in self.server.key_events if down == 1
        )
        self.assertEqual(typed, "ok rồi")
        # Quan trọng: phiên vẫn sống sau ký tự lạ.
        self.assertIs(self.session.state, State.ONLINE)

    async def test_modifier_combo_holds_then_releases_in_reverse(self) -> None:
        self.server.key_events.clear()
        self.session.press_keys("Ctrl", "c")
        self.assertTrue(await self.drain(lambda: len(self.server.key_events) >= 4))

        events = self.server.key_events[:4]
        self.assertEqual(events[0], (1, CTRL_KEYSYM), "Ctrl phải nhấn trước")
        self.assertEqual(events[1][0], 1)
        self.assertEqual(events[1][1], ord("c"))
        self.assertEqual(events[2][0], 0)
        self.assertEqual(events[2][1], ord("c"), "phải nhả 'c' trước Ctrl")
        self.assertEqual(events[3], (0, CTRL_KEYSYM))

    async def test_unknown_keysym_is_refused_without_killing_the_session(self) -> None:
        self.session.press_keys("KhongCoPhimNay")
        await asyncio.sleep(0.3)
        self.assertIs(self.session.state, State.ONLINE)


class DetailViewInputTest(unittest.TestCase):
    """Widget dịch sự kiện Qt sang toạ độ framebuffer."""

    def setUp(self) -> None:
        self.view = DetailView()
        self.view.resize(200, 400)
        self.view.set_device("1.2.3.4:5901")
        # Một khung 100x200 để widget có tỉ lệ và vùng vẽ hợp lệ.
        self.view.on_frame(Frame(key="1.2.3.4:5901", width=100, height=200,
                                 data=bytes(100 * 200 * 3),
                                 full_width=100, full_height=200))
        self.view.show()
        app.processEvents()
        self.centre = self.view._target.center()

    def tearDown(self) -> None:
        self.view.close()

    def _press(self, button=Qt.LeftButton, pos=None):
        pos = QPointF(pos or self.centre)
        return QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos, button, button,
                           Qt.NoModifier)

    def _release(self, button=Qt.LeftButton, pos=None):
        pos = QPointF(pos or self.centre)
        return QMouseEvent(QMouseEvent.Type.MouseButtonRelease, pos, button,
                           Qt.NoButton, Qt.NoModifier)

    def test_right_click_reports_button_two(self) -> None:
        seen = []
        self.view.pointer_pressed.connect(lambda x, y, b: seen.append(b))
        self.view.mousePressEvent(self._press(Qt.RightButton))
        self.assertEqual(seen, [2])

    def test_middle_click_reports_button_one(self) -> None:
        seen = []
        self.view.pointer_pressed.connect(lambda x, y, b: seen.append(b))
        self.view.mousePressEvent(self._press(Qt.MiddleButton))
        self.assertEqual(seen, [1])

    def test_press_maps_to_framebuffer_centre(self) -> None:
        seen = []
        self.view.pointer_pressed.connect(lambda x, y, b: seen.append((x, y)))
        self.view.mousePressEvent(self._press())
        self.assertEqual(len(seen), 1)
        x, y = seen[0]
        self.assertAlmostEqual(x, 50, delta=2)
        self.assertAlmostEqual(y, 100, delta=2)

    def test_wheel_emits_scroll_steps(self) -> None:
        seen = []
        self.view.scrolled.connect(lambda x, y, dx, dy: seen.append((dx, dy)))
        event = QWheelEvent(
            QPointF(self.centre), QPointF(self.centre), QPoint(0, 0), QPoint(0, 240),
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
        )
        self.view.wheelEvent(event)
        self.assertEqual(seen, [(0, 2)])

    def test_small_wheel_nudge_still_scrolls(self) -> None:
        """Nhích ít hơn một nấc vẫn phải cuộn, nếu không cảm giác là kẹt."""

        seen = []
        self.view.scrolled.connect(lambda x, y, dx, dy: seen.append((dx, dy)))
        event = QWheelEvent(
            QPointF(self.centre), QPointF(self.centre), QPoint(0, 0), QPoint(0, -30),
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
        )
        self.view.wheelEvent(event)
        self.assertEqual(seen, [(0, -1)])

    def test_plain_character_is_text_not_a_key_combo(self) -> None:
        typed, combos = [], []
        self.view.text_typed.connect(typed.append)
        self.view.keys_pressed.connect(combos.append)
        self.view.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_A, Qt.NoModifier, "a")
        )
        self.assertEqual(typed, ["a"])
        self.assertEqual(combos, [])

    def test_ctrl_c_becomes_a_combo_not_a_control_character(self) -> None:
        typed, combos = [], []
        self.view.text_typed.connect(typed.append)
        self.view.keys_pressed.connect(combos.append)
        self.view.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_C, Qt.ControlModifier, "\x03")
        )
        self.assertEqual(combos, [["Ctrl", "c"]])
        self.assertEqual(typed, [])

    def test_special_keys_are_named(self) -> None:
        combos = []
        self.view.keys_pressed.connect(combos.append)
        for code, expected in [(Qt.Key_Return, "Return"), (Qt.Key_Backspace, "BackSpace"),
                               (Qt.Key_PageDown, "Page_Down"), (Qt.Key_F5, "F5")]:
            combos.clear()
            self.view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, code,
                                              Qt.NoModifier, ""))
            self.assertEqual(combos, [[expected]])

    def test_shift_enter_carries_the_modifier(self) -> None:
        combos = []
        self.view.keys_pressed.connect(combos.append)
        self.view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Return,
                                          Qt.ShiftModifier, ""))
        self.assertEqual(combos, [["Shift", "Return"]])

    def test_cursor_marker_follows_the_pointer(self) -> None:
        self.assertIsNone(self.view._cursor)
        self.view.mousePressEvent(self._press())
        self.assertIsNotNone(self.view._cursor)
        self.assertTrue(self.view._cursor_down)
        self.view.mouseReleaseEvent(self._release())
        self.assertFalse(self.view._cursor_down)

    def test_clicks_outside_the_image_are_ignored(self) -> None:
        seen = []
        self.view.pointer_pressed.connect(lambda *a: seen.append(a))
        outside = QPoint(self.view._target.left() - 20, self.centre.y())
        self.view.mousePressEvent(self._press(pos=outside))
        self.assertEqual(seen, [], "bấm ra ngoài ảnh không được gửi gì tới máy")


class BroadcastInputTest(unittest.TestCase):
    """Thao tác phát cho nhiều máy: kéo phải ra vuốt, không phải một cú chạm."""

    def setUp(self) -> None:
        from controlios.config import Registry
        from controlios.ui.app import MainWindow

        self.registry_path = Path(__file__).parent / "_broadcast_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        registry.save(self.registry_path)

        self.window = MainWindow(self.registry_path)
        self.window.detail.set_device("10.0.0.1:5901")
        self.window.detail.on_frame(Frame(key="10.0.0.1:5901", width=100, height=200,
                                          data=bytes(100 * 200 * 3),
                                          full_width=100, full_height=200))
        self.window.grid.select_all()
        self.window.broadcast_box.setChecked(True)

        self.taps, self.swipes, self.scrolls = [], [], []
        self.window.pool.broadcast_tap = lambda k, rx, ry: self.taps.append((rx, ry))
        self.window.pool.broadcast_swipe = \
            lambda k, r1, r2, d: self.swipes.append((r1, r2, d))
        self.window.pool.broadcast_scroll = \
            lambda k, rx, ry, dx, dy: self.scrolls.append((dx, dy))

    def tearDown(self) -> None:
        self.window.close()
        self.registry_path.unlink(missing_ok=True)

    def test_short_press_broadcasts_a_tap(self) -> None:
        self.window._on_pointer_pressed(50, 100, 0)
        self.window._on_pointer_released(52, 101, 0)

        self.assertEqual(len(self.taps), 1)
        self.assertFalse(self.swipes)
        rx, ry = self.taps[0]
        self.assertAlmostEqual(rx, 0.52, delta=0.01)
        self.assertAlmostEqual(ry, 0.505, delta=0.01)

    def test_long_drag_broadcasts_a_swipe(self) -> None:
        self.window._on_pointer_pressed(50, 180, 0)
        self.window._on_pointer_released(50, 40, 0)

        self.assertEqual(len(self.swipes), 1, "kéo dài phải thành vuốt")
        self.assertFalse(self.taps, "không được co cú kéo thành một cú chạm")
        start, end, duration = self.swipes[0]
        self.assertAlmostEqual(start[1], 0.9, delta=0.01)
        self.assertAlmostEqual(end[1], 0.2, delta=0.01)
        self.assertGreaterEqual(duration, 0.1)

    def test_wheel_broadcasts_a_scroll(self) -> None:
        self.window._on_scrolled(50, 100, 0, -3)
        self.assertEqual(self.scrolls, [(0, -3)])

    def test_without_broadcast_input_goes_to_the_focused_device_only(self) -> None:
        self.window.broadcast_box.setChecked(False)
        singles = []
        self.window.pool.mouse_down = lambda k, x, y, b: singles.append(("down", k, b))
        self.window.pool.mouse_up = lambda k, x, y, b: singles.append(("up", k, b))
        self.window.pool.scroll = lambda k, x, y, dx, dy: singles.append(("scroll", k, dy))

        self.window._on_pointer_pressed(50, 100, 2)
        self.window._on_pointer_released(50, 100, 2)
        self.window._on_scrolled(50, 100, 0, 1)

        self.assertEqual(singles, [
            ("down", "10.0.0.1:5901", 2),
            ("up", "10.0.0.1:5901", 2),
            ("scroll", "10.0.0.1:5901", 1),
        ])
        self.assertFalse(self.taps + self.swipes + self.scrolls)

    def test_pointer_move_shows_pixels_and_ratios(self) -> None:
        self.window._on_pointer_moved(25, 150)
        text = self.window.coords_label.text()
        self.assertIn("x=25", text)
        self.assertIn("y=150", text)
        self.assertIn("0.250", text)
        self.assertIn("0.750", text)

    def test_send_text_dialog_types_then_presses_enter(self) -> None:
        from controlios.ui.app import QDialog, SendTextDialog

        sent, keys = [], []
        self.window.pool.type_text = lambda t, text, **kw: sent.append((list(t), text))
        self.window.pool.press_keys = lambda t, *k: keys.append(k)

        with unittest.mock.patch.object(SendTextDialog, "exec",
                                        return_value=QDialog.Accepted), \
             unittest.mock.patch.object(SendTextDialog, "delivery",
                                        return_value=("Xin chào", False, False)), \
             unittest.mock.patch.object(SendTextDialog, "result_text",
                                        return_value=("Xin chào", True)):
            self.window._send_text_dialog()

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "Xin chào")
        self.assertEqual(len(sent[0][0]), 3, "phải gửi cho cả 3 máy đang chọn")
        self.assertEqual(keys, [("Return",)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
