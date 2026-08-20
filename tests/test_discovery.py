"""Dò máy qua Bonjour (_rfb._tcp) — TrollVNC tự quảng bá dịch vụ này."""

from __future__ import annotations

import asyncio
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios import scan                                # noqa: E402


class FakeInfo:
    def __init__(self, addresses, port) -> None:
        self._addresses = addresses
        self.port = port

    def parsed_addresses(self):
        return self._addresses


class FakeZeroconf:
    """Giả lập zeroconf: gọi listener ngay khi ServiceBrowser được tạo."""

    services = {
        "iPhone-01._rfb._tcp.local.": FakeInfo(["172.30.3.152"], 5901),
        "iPhone-02._rfb._tcp.local.": FakeInfo(["172.30.3.101"], 5902),
        "iPad._rfb._tcp.local.": FakeInfo(["10.9.9.9"], 5901),
        "v6-only._rfb._tcp.local.": FakeInfo(["fe80::1"], 5901),
        "no-info._rfb._tcp.local.": None,
    }

    def __init__(self) -> None:
        self.closed = False

    def get_service_info(self, service_type, name, timeout=None):
        return self.services.get(name)

    def close(self) -> None:
        self.closed = True


class BonjourTest(unittest.TestCase):
    def _patched(self, prefix: str = "") -> list[str]:
        zeroconf_instance = FakeZeroconf()

        class FakeBrowser:
            def __init__(self, zc, service_type, listener) -> None:
                self.service_type = service_type
                for name in FakeZeroconf.services:
                    listener.add_service(zc, service_type, name)

            def cancel(self) -> None:
                pass

        module = unittest.mock.MagicMock()
        module.Zeroconf = lambda: zeroconf_instance
        module.ServiceBrowser = FakeBrowser
        module.ServiceListener = object

        with unittest.mock.patch.dict(sys.modules, {"zeroconf": module}), \
             unittest.mock.patch.object(scan.time, "sleep", lambda _s: None):
            result = scan.discover_bonjour(timeout=0.01, prefix=prefix)

        self.assertTrue(zeroconf_instance.closed, "phải đóng zeroconf sau khi xong")
        return result

    def test_finds_hosts_with_their_advertised_port(self) -> None:
        result = self._patched()
        self.assertIn("172.30.3.152:5901", result)
        self.assertIn("172.30.3.101:5902", result, "phải lấy đúng cổng máy quảng bá")

    def test_results_are_sorted_by_address(self) -> None:
        result = self._patched()
        self.assertLess(result.index("172.30.3.101:5902"),
                        result.index("172.30.3.152:5901"))

    def test_ipv6_and_missing_info_are_skipped(self) -> None:
        result = self._patched()
        self.assertFalse([r for r in result if r.startswith("fe80")])
        self.assertEqual(len(result), 3)

    def test_prefix_filters_other_subnets(self) -> None:
        result = self._patched(prefix="172.30.")
        self.assertEqual(len(result), 2)
        self.assertFalse([r for r in result if r.startswith("10.9")])

    def test_missing_zeroconf_is_not_fatal(self) -> None:
        """Thiếu thư viện thì trả rỗng để phần quét thường vẫn chạy."""

        with unittest.mock.patch.dict(sys.modules, {"zeroconf": None}):
            self.assertEqual(scan.discover_bonjour(timeout=0.01), [])

    def test_service_type_matches_what_trollvnc_advertises(self) -> None:
        self.assertEqual(scan.BONJOUR_SERVICE, "_rfb._tcp.local.")


class MergeTest(unittest.TestCase):
    def test_registry_accepts_host_with_port_from_bonjour(self) -> None:
        from controlios.config import Registry

        registry = Registry()
        added = registry.merge_hosts(["172.30.3.152:5901", "172.30.3.101:5902"])
        self.assertEqual(added, 2)
        self.assertEqual([(d.host, d.port) for d in registry.devices],
                         [("172.30.3.152", 5901), ("172.30.3.101", 5902)])

    def test_same_host_on_two_ports_is_two_devices(self) -> None:
        from controlios.config import Registry

        registry = Registry()
        registry.merge_hosts(["172.30.3.152:5901", "172.30.3.152:5902"])
        self.assertEqual(len(registry.devices), 2)

    def test_merging_twice_adds_nothing(self) -> None:
        from controlios.config import Registry

        registry = Registry()
        registry.merge_hosts(["172.30.3.152:5901"])
        self.assertEqual(registry.merge_hosts(["172.30.3.152:5901"]), 0)


class ProbeTest(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_control_socket_when_rfb_has_no_banner(self) -> None:
        reader = unittest.mock.AsyncMock()
        reader.readexactly.side_effect = asyncio.IncompleteReadError(b"", 4)
        writer = unittest.mock.MagicMock()
        writer.wait_closed = unittest.mock.AsyncMock()

        with unittest.mock.patch.object(
            scan.asyncio, "open_connection",
            side_effect=[(reader, writer), (unittest.mock.Mock(), writer)],
        ) as connect:
            self.assertTrue(await scan._probe("172.30.3.152", 5901, 0.1))

        self.assertEqual(connect.call_args_list[1].args,
                         ("172.30.3.152", scan.CONTROL_DISCOVERY_PORT))


if __name__ == "__main__":
    unittest.main(verbosity=2)
