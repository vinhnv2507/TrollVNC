"""Điều khiển thẳng trên ô lưới, khỏi phải mở khung riêng."""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QPointF, Qt          # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent      # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

from controlios.config import DeviceSpec, Registry      # noqa: E402
from controlios.ui.grid import DeviceGrid               # noqa: E402
from controlios.vnc.session import Frame, Tier          # noqa: E402

app = QApplication.instance() or QApplication([])

PHONE_W, PHONE_H = 752, 1338


def frame(key: str) -> Frame:
    return Frame(key=key, width=94, height=167, data=bytes(94 * 167 * 3),
                 full_width=PHONE_W, full_height=PHONE_H)


class TileControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = DeviceGrid(tile_width=150)
        self.grid.resize(700, 600)
        self.specs = [DeviceSpec(host=f"10.0.0.{i}") for i in range(1, 5)]
        self.grid.set_devices(self.specs)
        self.grid.show()
        app.processEvents()

        self.key = self.specs[0].key
        self.tile = self.grid.tiles[self.key]
        self.tile.set_frame(frame(self.key))
        self.tile.grab()                    # buộc vẽ để có _image_rect
        app.processEvents()

    def tearDown(self) -> None:
        self.grid.close()

    def _press(self, pos, button=Qt.LeftButton, modifiers=Qt.NoModifier):
        return QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(pos),
                           button, button, modifiers)

    def _release(self, pos, button=Qt.LeftButton):
        return QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(pos),
                           button, Qt.NoButton, Qt.NoModifier)

    def test_off_by_default_so_clicking_still_selects(self) -> None:
        self.assertFalse(self.grid.control_enabled)
        selected, controlled = [], []
        self.grid.selection_changed.connect(selected.append)
        self.grid.tile_pressed.connect(lambda *a: controlled.append(a))

        self.tile.mousePressEvent(self._press(self.tile._image_rect.center()))

        self.assertTrue(selected)
        self.assertFalse(controlled)

    def test_context_menu_targets_one_or_keeps_existing_group(self) -> None:
        second = self.specs[1].key
        third = self.specs[2].key
        self.grid.selection = [self.key, second]
        self.grid.tiles[self.key].set_selected(True)
        self.grid.tiles[second].set_selected(True)

        self.assertEqual(self.grid._context_targets(second), [self.key, second])
        self.assertEqual(self.grid._context_targets(third), [third])
        self.assertTrue(self.grid.tiles[third].selected)
        self.assertFalse(self.grid.tiles[self.key].selected)

    def test_enabled_sends_input_instead_of_selecting(self) -> None:
        self.grid.set_control_enabled(True)
        selected, controlled = [], []
        self.grid.selection_changed.connect(selected.append)
        self.grid.tile_pressed.connect(lambda *a: controlled.append(a))

        self.tile.mousePressEvent(self._press(self.tile._image_rect.center()))

        self.assertFalse(selected, "bật điều khiển thì bấm không được chọn nữa")
        self.assertEqual(len(controlled), 1)

    def test_coordinates_map_to_the_real_framebuffer(self) -> None:
        self.grid.set_control_enabled(True)
        seen = []
        self.grid.tile_pressed.connect(lambda k, x, y, b: seen.append((k, x, y)))

        self.tile.mousePressEvent(self._press(self.tile._image_rect.center()))

        key, x, y = seen[0]
        self.assertEqual(key, self.key)
        # Bấm giữa ô -> giữa màn hình máy, dù ô chỉ rộng ~150 px.
        self.assertAlmostEqual(x, PHONE_W // 2, delta=PHONE_W * 0.03)
        self.assertAlmostEqual(y, PHONE_H // 2, delta=PHONE_H * 0.03)

    def test_ctrl_click_still_selects_while_control_is_on(self) -> None:
        """Nếu không thì bật chế độ này lên là hết chọn được máy."""

        self.grid.set_control_enabled(True)
        selected, controlled = [], []
        self.grid.selection_changed.connect(selected.append)
        self.grid.tile_pressed.connect(lambda *a: controlled.append(a))

        self.tile.mousePressEvent(
            self._press(self.tile._image_rect.center(), modifiers=Qt.ControlModifier)
        )

        self.assertTrue(selected)
        self.assertFalse(controlled)

    def test_drag_and_release_are_forwarded(self) -> None:
        self.grid.set_control_enabled(True)
        moved, released = [], []
        self.grid.tile_moved.connect(lambda *a: moved.append(a))
        self.grid.tile_released.connect(lambda *a: released.append(a))

        rect = self.tile._image_rect
        self.tile.mousePressEvent(self._press(rect.center()))
        self.tile.mouseMoveEvent(
            QMouseEvent(QMouseEvent.Type.MouseMove,
                        QPointF(rect.center().x(), rect.center().y() - 20),
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        )
        self.tile.mouseReleaseEvent(self._release(rect.topLeft() + QPoint(5, 5)))

        self.assertEqual(len(moved), 1)
        self.assertEqual(len(released), 1)
        # Nhả gần góc trên trái -> toạ độ nhỏ.
        self.assertLess(released[0][2], PHONE_H * 0.2)

    def test_wheel_is_forwarded(self) -> None:
        self.grid.set_control_enabled(True)
        seen = []
        self.grid.tile_scrolled.connect(lambda k, x, y, dx, dy: seen.append((dx, dy)))

        centre = self.tile._image_rect.center()
        self.tile.wheelEvent(QWheelEvent(
            QPointF(centre), QPointF(centre), QPoint(0, 0), QPoint(0, -240),
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
        ))

        self.assertEqual(seen, [(0, -2)])

    def test_clicks_outside_the_image_send_nothing(self) -> None:
        self.grid.set_control_enabled(True)
        seen = []
        self.grid.tile_pressed.connect(lambda *a: seen.append(a))

        # Dải nhãn dưới đáy ô nằm ngoài vùng ảnh.
        self.tile.mousePressEvent(self._press(QPoint(5, self.tile.height() - 5)))

        self.assertFalse(seen)

    def test_double_click_still_opens_the_detail_pane(self) -> None:
        self.grid.set_control_enabled(True)
        opened = []
        self.grid.device_activated.connect(opened.append)

        self.tile.mouseDoubleClickEvent(self._press(self.tile._image_rect.center()))

        self.assertEqual(opened, [self.key])

    def test_touched_tile_is_boosted_then_released(self) -> None:
        """Ô lưới chạy 1 hình/giây, bấm vào mà chờ một giây thì không dùng được."""

        self.grid.set_control_enabled(True)
        published = []
        self.grid.tiers_changed.connect(published.append)

        self.tile.mousePressEvent(self._press(self.tile._image_rect.center()))
        self.assertTrue(published)
        self.assertEqual(published[-1][self.key], Tier.LIVE)

        self.grid._release_control_boost()
        self.assertNotEqual(published[-1][self.key], Tier.LIVE)

    def test_turning_control_off_drops_the_boost(self) -> None:
        self.grid.set_control_enabled(True)
        self.tile.mousePressEvent(self._press(self.tile._image_rect.center()))
        self.assertEqual(self.grid._control_key, self.key)

        self.grid.set_control_enabled(False)
        self.assertIsNone(self.grid._control_key)

    def test_new_devices_inherit_the_mode(self) -> None:
        self.grid.set_control_enabled(True)
        self.grid.set_devices([DeviceSpec(host="10.0.0.9")])
        tile = self.grid.tiles["10.0.0.9:5901"]
        self.assertTrue(tile.control_enabled)

    def test_earnapp_monitor_marks_only_its_own_devices(self) -> None:
        other = self.specs[1].key
        self.grid.set_monitored_keys([self.key])
        self.assertTrue(self.grid.tiles[self.key].monitored)
        self.assertFalse(self.grid.tiles[other].monitored)
        self.assertTrue(self.grid._monitor_timer.isActive())

        self.grid.set_monitored_keys([])
        self.assertFalse(self.grid.tiles[self.key].monitored)
        self.assertFalse(self.grid._monitor_timer.isActive())

    def test_monitor_marks_survive_grid_rebuild(self) -> None:
        self.grid.set_monitored_keys([self.key])
        self.grid.set_devices(self.specs)
        self.assertTrue(self.grid.tiles[self.key].monitored)


class WindowGridControlTest(unittest.TestCase):
    def setUp(self) -> None:
        from controlios.ui.app import MainWindow

        self.path = Path(__file__).parent / "_gridctl_devices.json"
        registry = Registry()
        registry.merge_hosts(["10.0.0.1", "10.0.0.2"])
        registry.save(self.path)
        self.window = MainWindow(self.path)
        self.key = "10.0.0.1:5901"
        self.window.grid.tiles[self.key].set_frame(frame(self.key))

    def tearDown(self) -> None:
        self.window.close()
        self.path.unlink(missing_ok=True)

    def test_toolbar_toggle_drives_the_grid(self) -> None:
        self.window.grid_control_box.setChecked(True)
        self.assertTrue(self.window.grid.control_enabled)
        self.window.grid_control_box.setChecked(False)
        self.assertFalse(self.window.grid.control_enabled)

    def test_press_release_goes_to_that_device_only(self) -> None:
        sent = []
        self.window.pool.mouse_down = lambda k, x, y, b: sent.append(("down", k, x, y))
        self.window.pool.mouse_up = lambda k, x, y, b: sent.append(("up", k, x, y))

        self.window._on_tile_pressed(self.key, 100, 200, 0)
        self.window._on_tile_released(self.key, 100, 200, 0)

        self.assertEqual(sent, [("down", self.key, 100, 200), ("up", self.key, 100, 200)])

    def test_broadcast_turns_a_tile_drag_into_a_swipe_for_everyone(self) -> None:
        swipes, taps = [], []
        self.window.pool.broadcast_swipe = lambda k, a, b, d: swipes.append((a, b))
        self.window.pool.broadcast_tap = lambda k, rx, ry: taps.append((rx, ry))
        self.window.grid.select_all()
        self.window.broadcast_box.setChecked(True)

        self.window._on_tile_pressed(self.key, 376, 1200, 0)
        self.window._on_tile_released(self.key, 376, 200, 0)

        self.assertEqual(len(swipes), 1)
        self.assertFalse(taps)
        start, end = swipes[0]
        self.assertAlmostEqual(start[1], 1200 / PHONE_H, places=2)
        self.assertAlmostEqual(end[1], 200 / PHONE_H, places=2)

    def test_broadcast_short_press_is_a_tap(self) -> None:
        taps = []
        self.window.pool.broadcast_tap = lambda k, rx, ry: taps.append((rx, ry))
        self.window.pool.broadcast_swipe = lambda *a: self.fail("không được thành vuốt")
        self.window.grid.select_all()
        self.window.broadcast_box.setChecked(True)

        self.window._on_tile_pressed(self.key, 376, 669, 0)
        self.window._on_tile_released(self.key, 378, 671, 0)

        self.assertEqual(len(taps), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
