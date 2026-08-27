from __future__ import annotations

import asyncio
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.config import Settings  # noqa: E402
from controlios.control_channel import AppNetworkSample  # noqa: E402
from controlios.vnc.pool import DevicePool  # noqa: E402
class FakeTrafficChannel:
    def __init__(self, samples: list[AppNetworkSample]) -> None:
        self.samples = list(samples)

    async def app_network_sample(self, _bundle_id: str) -> AppNetworkSample:
        return self.samples.pop(0)


class EarnAppTrafficMonitorTest(unittest.TestCase):
    def run_monitor(self, samples: list[AppNetworkSample]) -> tuple[dict, list[str]]:
        pool = DevicePool(Settings(), lambda *_args: None, lambda *_args: None)
        channel = FakeTrafficChannel(samples)
        captured = []
        events: list[str] = []
        done = []

        pool._channel = lambda _key: channel  # type: ignore[method-assign]
        pool._call_coro = lambda coro: captured.append(coro)  # type: ignore[method-assign]

        pool.measure_app_traffic(
            ["phone-1"], "com.brd.earnapp",
            sample_seconds=3, min_rx_bytes=32 * 1024,
            on_event=lambda _key, message: events.append(message),
            on_done=lambda _total, summary, failures: done.append((summary, failures)),
        )

        async def no_sleep(_seconds: float) -> None:
            return None

        with unittest.mock.patch("controlios.vnc.pool.asyncio.sleep", no_sleep):
            asyncio.run(captured[0])
        self.assertFalse(done[0][1])
        return done[0][0], events

    def test_reports_sharing_when_rx_grows_and_app_has_socket(self) -> None:
        summary, events = self.run_monitor([
            AppNetworkSample(123, 1_000_000, 500_000, 2, 1_000),
            AppNetworkSample(123, 1_065_536, 510_000, 2, 4_000),
        ])

        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["inactive"], 0)
        self.assertTrue(any("ĐANG CHIA SẺ BĂNG THÔNG" in event for event in events))

    def test_does_not_claim_sharing_without_an_earnapp_socket(self) -> None:
        summary, events = self.run_monitor([
            AppNetworkSample(123, 1_000_000, 500_000, 0, 1_000),
            AppNetworkSample(123, 2_000_000, 900_000, 0, 4_000),
        ])

        self.assertEqual(summary["active"], 0)
        self.assertEqual(summary["inactive"], 1)
        self.assertTrue(any("CHƯA THẤY CHIA SẺ" in event for event in events))


if __name__ == "__main__":
    unittest.main()
