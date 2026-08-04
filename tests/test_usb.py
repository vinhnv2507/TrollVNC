"""Chế độ USB: phân cổng, liệt kê máy, dựng relay (mock subprocess)."""

from __future__ import annotations

import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios import usb                                       # noqa: E402
from controlios.config import DeviceSpec                         # noqa: E402


class PortsTest(unittest.TestCase):
    def test_each_device_gets_a_distinct_block(self) -> None:
        self.assertEqual(usb.ports_for(0), (6001, 6002, 6003))
        self.assertEqual(usb.ports_for(1), (6011, 6012, 6013))
        self.assertEqual(usb.ports_for(2), (6021, 6022, 6023))


class ListDevicesTest(unittest.TestCase):
    def _run(self, stdout: str, returncode: int = 0):
        completed = subprocess.CompletedProcess([], returncode, stdout, "")
        with unittest.mock.patch("controlios.usb.subprocess.run", return_value=completed):
            return usb.list_usb_devices()

    def test_parses_json(self) -> None:
        out = ('[{"udid": "abc123", "name": "iPhone A", "serial": "S1"},'
               ' {"udid": "def456", "name": "iPhone B", "serial": "S2"}]')
        devices = self._run(out)
        self.assertEqual([d["udid"] for d in devices], ["abc123", "def456"])
        self.assertEqual(devices[0]["name"], "iPhone A")

    def test_empty_and_bad_json_return_empty(self) -> None:
        self.assertEqual(self._run("[]"), [])
        self.assertEqual(self._run("not json"), [])
        self.assertEqual(self._run("[]", returncode=1), [])


class RelayManagerTest(unittest.TestCase):
    def test_start_for_spawns_three_ports_and_returns_specs(self) -> None:
        manager = usb.UsbRelayManager()
        spawned = []
        with unittest.mock.patch.object(
            manager, "_spawn", side_effect=lambda u, l, r: spawned.append((u, l, r))
        ):
            specs = manager.start_for(
                [{"udid": "abc", "name": "A"}, {"udid": "def", "name": "B"}]
            )

        # 2 máy × 3 cổng
        self.assertEqual(len(spawned), 6)
        self.assertIn(("abc", 6001, usb.DEVICE_VNC_PORT), spawned)
        self.assertIn(("abc", 6002, usb.DEVICE_CONTROL_PORT), spawned)
        self.assertIn(("abc", 6003, usb.DEVICE_SSH_PORT), spawned)
        self.assertIn(("def", 6011, usb.DEVICE_VNC_PORT), spawned)

        self.assertEqual(specs[0], {
            "host": "127.0.0.1", "port": 6001, "control_port": 6002,
            "ssh_port": 6003, "name": "A", "udid": "abc", "group": "usb",
        })

    def test_restore_uses_saved_ports(self) -> None:
        manager = usb.UsbRelayManager()
        spawned = []
        spec = DeviceSpec(host="127.0.0.1", port=6011, control_port=6012,
                          ssh_port=6013, udid="xyz")
        with unittest.mock.patch.object(
            manager, "_spawn", side_effect=lambda u, l, r: spawned.append((u, l, r))
        ):
            manager.restore([spec])
        self.assertIn(("xyz", 6011, usb.DEVICE_VNC_PORT), spawned)
        self.assertIn(("xyz", 6012, usb.DEVICE_CONTROL_PORT), spawned)
        self.assertIn(("xyz", 6013, usb.DEVICE_SSH_PORT), spawned)


class DeviceSpecUsbTest(unittest.TestCase):
    def test_is_usb_and_defaults(self) -> None:
        wifi = DeviceSpec(host="172.30.3.5")
        self.assertFalse(wifi.is_usb)
        self.assertIsNone(wifi.control_port)
        self.assertIsNone(wifi.ssh_port)

        cabled = DeviceSpec(host="127.0.0.1", port=6001, control_port=6002,
                            udid="abc")
        self.assertTrue(cabled.is_usb)
        self.assertEqual(cabled.control_port, 6002)


class PoolPortSelectionTest(unittest.TestCase):
    """Pool phải dùng cổng control/SSH riêng của máy USB, không thì cổng chung."""

    class _FakeSession:
        def __init__(self, spec):
            self.spec = spec

    def _pool(self):
        from controlios.vnc.pool import DevicePool
        from controlios.config import Settings
        return DevicePool(Settings(), on_frame=lambda *a: None,
                          on_status=lambda *a: None)

    def test_usb_device_uses_its_own_ports(self) -> None:
        pool = self._pool()
        spec = DeviceSpec(host="127.0.0.1", port=6001, control_port=6002,
                          ssh_port=6003, udid="x")
        pool._sessions["127.0.0.1:6001"] = self._FakeSession(spec)
        self.assertEqual(pool._channel("127.0.0.1:6001").port, 6002)
        self.assertEqual(pool._ssh("127.0.0.1:6001").port, 6003)

    def test_wifi_device_uses_global_ports(self) -> None:
        pool = self._pool()
        spec = DeviceSpec(host="172.30.3.5")
        pool._sessions["172.30.3.5:5901"] = self._FakeSession(spec)
        self.assertEqual(pool._channel("172.30.3.5:5901").port,
                         pool.settings.control_port)
        self.assertEqual(pool._ssh("172.30.3.5:5901").port, pool.settings.ssh_port)


if __name__ == "__main__":
    unittest.main(verbosity=2)
