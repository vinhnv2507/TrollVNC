"""Offscreen smoke test: the window builds, tiles paint, tiers get published."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication              # noqa: E402

from controlios.config import DeviceSpec, Registry      # noqa: E402
from controlios.vnc.session import Frame, State, Tier   # noqa: E402
from controlios.ui.grid import DeviceGrid               # noqa: E402
from controlios.ui.app import MainWindow                # noqa: E402

app = QApplication.instance() or QApplication([])


class GridTest(unittest.TestCase):
    def test_tiers_only_promote_visible_tiles(self) -> None:
        grid = DeviceGrid(tile_width=150)
        grid.resize(700, 400)
        specs = [DeviceSpec(host=f"10.0.0.{i}") for i in range(1, 101)]
        grid.set_devices(specs)
        grid.show()
        app.processEvents()

        published: dict = {}
        grid.tiers_changed.connect(published.update)
        grid._publish_tiers()

        self.assertEqual(len(published), 100)
        promoted = [k for k, t in published.items() if t is not Tier.IDLE]
        self.assertGreater(len(promoted), 0, "nothing was promoted for the viewport")
        self.assertLess(len(promoted), 100,
                        "every tile was promoted — virtualisation is not working")

    def test_frame_and_status_reach_the_tile(self) -> None:
        grid = DeviceGrid()
        spec = DeviceSpec(host="10.0.0.7")
        grid.set_devices([spec])
        frame = Frame(key=spec.key, width=4, height=8, data=bytes(4 * 8 * 3),
                      full_width=375, full_height=667)
        grid.on_frame(frame)
        grid.on_status(spec.key, State.ONLINE, "")
        tile = grid.tiles[spec.key]
        self.assertEqual(tile.state, State.ONLINE)
        self.assertIsNotNone(tile._pixmap)

    def test_selection_modes(self) -> None:
        from PySide6.QtCore import Qt

        grid = DeviceGrid()
        specs = [DeviceSpec(host=f"10.0.0.{i}") for i in range(1, 6)]
        grid.set_devices(specs)

        grid._on_tile_clicked(specs[0].key, Qt.NoModifier)
        self.assertEqual(grid.selection, [specs[0].key])
        grid._on_tile_clicked(specs[3].key, Qt.ShiftModifier)
        self.assertEqual(len(grid.selection), 4)
        grid._on_tile_clicked(specs[3].key, Qt.ControlModifier)
        self.assertEqual(len(grid.selection), 3)
        grid.clear_selection()
        self.assertEqual(grid.selection, [])


class WindowTest(unittest.TestCase):
    def test_window_builds_and_pages(self) -> None:
        registry_path = Path(__file__).parent / "_smoke_devices.json"
        registry = Registry()
        registry.merge_hosts([f"10.0.0.{i}" for i in range(1, 121)])
        registry.save(registry_path)

        window = MainWindow(registry_path)
        try:
            window.show()
            app.processEvents()
            self.assertEqual(window._pages(), 2)          # 120 devices, 100 per page
            self.assertEqual(len(window.grid.tiles), 100)
            window._go_page(1)
            self.assertEqual(len(window.grid.tiles), 20)
        finally:
            window.close()
            registry_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
