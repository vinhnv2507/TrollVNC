"""Refresh cached phone metadata and preserve explicit PC display names."""
import os
import unittest
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from controlios.config import DeviceSpec, Registry
from controlios.control_channel import ControlError
from controlios.ui.app import DeviceNameWorker, MainWindow

app = QApplication.instance() or QApplication([])


class DeviceMetadataTest(unittest.TestCase):
    def test_close_cancels_an_offline_metadata_lookup(self):
        started = threading.Event()
        async def slow_version():
            started.set()
            await asyncio.sleep(60)
        channel = Mock(server_version=AsyncMock(side_effect=slow_version),
                       device_name=AsyncMock(return_value="Name"))
        worker = DeviceNameWorker([DeviceSpec("127.0.0.1")], 46752, "test")
        with patch("controlios.ui.app.ControlChannel", return_value=channel):
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                worker.stop()
                self.assertTrue(worker.wait(2000))
            finally:
                worker.stop()
                worker.wait(6000)
        channel.device_name.assert_not_called()

    def test_populated_cache_is_read_again(self):
        device = DeviceSpec("127.0.0.1", name="Old name", ios_version="4.14")
        channel = Mock(server_version=AsyncMock(return_value="4.15"),
                       device_name=AsyncMock(return_value="New name"))
        worker = DeviceNameWorker([device], 46752, "test")
        names, versions, refreshed = [], [], []
        worker.found.connect(lambda key, name: names.append((key, name)))
        worker.version.connect(lambda key, version: versions.append((key, version)))
        worker.refreshed.connect(refreshed.append)
        with patch("controlios.ui.app.ControlChannel", return_value=channel):
            worker.run()
        self.assertEqual(names, [(device.key, "New name")])
        self.assertEqual(versions, [(device.key, "4.15")])
        self.assertEqual(refreshed, [device.key])

    def test_failed_version_still_refreshes_name_and_stays_pending(self):
        device = DeviceSpec("127.0.0.1", name="Old name", ios_version="4.14")
        channel = Mock(server_version=AsyncMock(side_effect=ControlError("offline")),
                       device_name=AsyncMock(return_value="New name"))
        worker = DeviceNameWorker([device], 46752, "test")
        names, failures, refreshed = [], [], []
        worker.found.connect(lambda key, name: names.append(name))
        worker.failed.connect(lambda key, reason: failures.append(key))
        worker.refreshed.connect(refreshed.append)
        with patch("controlios.ui.app.ControlChannel", return_value=channel):
            worker.run()
        self.assertEqual(names, ["New name"])
        self.assertEqual(failures, [device.key])
        self.assertEqual(refreshed, [])

    def test_name_changes_save_and_custom_names_are_preserved(self):
        for custom in (False, True):
            with self.subTest(custom=custom):
                device = DeviceSpec("10.0.0.1", name="Old", custom_name=custom)
                owner = SimpleNamespace(registry=Registry(devices=[device]),
                                        registry_path="unused", _sync_tile_spec=Mock())
                with patch.object(Registry, "save") as save:
                    MainWindow._on_device_name_found(owner, device.key, "New")
                self.assertEqual(device.device_name, "New")
                self.assertEqual(device.name, "Old" if custom else "New")
                save.assert_called_once()

    def test_retry_selects_only_pending_enabled_devices(self):
        devices = [DeviceSpec("10.0.0.1", name="Cached", ios_version="4.14"),
                   DeviceSpec("10.0.0.2"), DeviceSpec("10.0.0.3", enabled=False)]
        owner = SimpleNamespace(
            registry=Registry(devices=devices), _device_name_worker=None,
            _metadata_pending={devices[0].key, devices[2].key},
            _on_device_name_found=Mock(), _on_device_version_found=Mock(),
            _on_device_name_failed=Mock(), _on_metadata_refreshed=Mock(),
            _device_name_worker_finished=Mock())
        with patch("controlios.ui.app.DeviceNameWorker") as constructor:
            MainWindow._refresh_device_names(owner, force=False)
        self.assertEqual(constructor.call_args.args[0], [devices[0]])


if __name__ == "__main__":
    unittest.main()
