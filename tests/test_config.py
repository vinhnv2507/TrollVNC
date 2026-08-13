"""Kiểm tra registry: validation và lưu nguyên tử."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from controlios.config import (
    DeviceSpec, Registry, Settings, load_named_scripts, save_named_scripts,
)


class SettingsValidationTest(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        settings = Settings()
        settings.validate()
        self.assertEqual(settings.device_scale, 0.35)
        self.assertTrue(settings.disconnect_offscreen)
        self.assertFalse(settings.focus_streaming)

    def test_rejects_zero_fps(self) -> None:
        with self.assertRaisesRegex(ValueError, "grid_fps"):
            Settings(grid_fps=0).validate()

    def test_rejects_negative_optional_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "không được âm"):
            Settings(max_connected=-1).validate()

    def test_rejects_backoff_max_below_initial_delay(self) -> None:
        with self.assertRaisesRegex(ValueError, "reconnect_max"):
            Settings(reconnect_delay=10, reconnect_max=5).validate()


class RegistryPersistenceTest(unittest.TestCase):
    def test_device_quality_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "devices.json"
            registry = Registry(devices=[DeviceSpec(
                "172.30.2.51", device_scale=0.35, live_fps=8,
                live_long_edge=640, device_low_latency=False)])
            registry.save(path)
            loaded = Registry.load(path).devices[0]
            self.assertEqual(loaded.device_scale, 0.35)
            self.assertEqual(loaded.live_fps, 8)
            self.assertEqual(loaded.live_long_edge, 640)
            self.assertFalse(loaded.device_low_latency)

    def test_round_trip_and_no_temporary_file_left(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "devices.json"
            registry = Registry(devices=[DeviceSpec("172.30.4.2", name="Máy 02")])

            registry.save(path)

            self.assertEqual(Registry.load(path), registry)
            self.assertEqual(list(path.parent.glob(".devices.json.*.tmp")), [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["devices"][0]["name"],
                             "Máy 02")

    def test_invalid_settings_are_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "devices.json"
            with self.assertRaises(ValueError):
                Registry(settings=Settings(ssh_port=0)).save(path)
            self.assertFalse(path.exists())


class NamedScriptsTest(unittest.TestCase):
    def test_round_trip_with_accents_and_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scripts.json"
            scripts = {"Mở Zalo": "launchapp com.zing.zalo\nwait 1-3\n",
                       "Chụp": "shot ket-qua"}
            save_named_scripts(scripts, path)
            self.assertEqual(load_named_scripts(path), scripts)
            self.assertEqual(list(path.parent.glob(".scripts.json.*.tmp")), [])

    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_named_scripts(Path(folder) / "khong-co.json"), {})

    def test_corrupt_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scripts.json"
            path.write_text("{ khong phai json", encoding="utf-8")
            self.assertEqual(load_named_scripts(path), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
