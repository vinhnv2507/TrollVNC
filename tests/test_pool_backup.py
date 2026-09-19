"""Regression tests for app backup/export flows."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from controlios.config import DeviceSpec, Settings
from controlios.vnc.pool import DevicePool


class _BackupChannel:
    def __init__(self, cookie_error: Exception | None = None) -> None:
        self.cookie_error = cookie_error
        self.calls: list[tuple] = []

    async def terminate(self, bundle_id: str) -> None:
        self.calls.append(("terminate", bundle_id))

    async def snapshot_app(self, bundle_id: str, name: str) -> str:
        self.calls.append(("snapshot", bundle_id, name))
        return name

    async def download_tree(self, remote: str, local: Path) -> int:
        self.calls.append(("download", remote, local))
        local.mkdir(parents=True, exist_ok=True)
        (local / "snapshot-marker.txt").write_text("snapshot", encoding="utf-8")
        return 8

    async def dump_cookies(self, bundle_id: str, dest: Path) -> dict:
        self.calls.append(("cookies", bundle_id, dest))
        if self.cookie_error:
            raise self.cookie_error
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "spc-st.txt").write_text("SPC_ST is intentionally not stored in this test", encoding="utf-8")
        return {"cookies": [{"name": "SPC_ST"}, {"name": "SPC_SI"}]}


class ShopeeBackupTest(unittest.TestCase):
    def _pool(self, channel: _BackupChannel) -> DevicePool:
        pool = DevicePool(Settings(), on_frame=lambda _frame: None,
                          on_status=lambda *_args: None)
        spec = DeviceSpec(host="172.30.3.42", port=5901, name="B42")
        pool._specs = {spec.key: spec}
        pool._sessions = {spec.key: SimpleNamespace(spec=spec)}
        pool._channel = lambda _key: channel  # type: ignore[method-assign]
        pool._call_coro = lambda coro: asyncio.run(coro)  # type: ignore[method-assign]
        return pool

    def test_shopee_backup_also_exports_cookie_folder_next_to_snapshot(self) -> None:
        channel = _BackupChannel()
        pool = self._pool(channel)
        events: list[str] = []
        done: list[tuple] = []
        with tempfile.TemporaryDirectory() as folder:
            pool.backup_app_to_pc(
                ["172.30.3.42:5901"], "com.beeasy.shopee.vn", "pc-test", folder,
                on_event=lambda _key, message: events.append(message),
                on_done=lambda description, ok, failures: done.append(
                    (description, ok, failures)),
            )
            root = Path(folder) / "B42_172.30.3.42-5901" / "com.beeasy.shopee.vn"
            self.assertTrue((root / "pc-test" / "snapshot-marker.txt").exists())
            self.assertTrue((root / "spc-st.txt").exists())
            self.assertEqual(done[0][1:], (1, []))
            self.assertIn("SPC_ST", events[0])
            self.assertEqual([call[0] for call in channel.calls],
                             ["terminate", "snapshot", "download", "cookies"])

    def test_cookie_export_failure_does_not_discard_valid_snapshot(self) -> None:
        channel = _BackupChannel(RuntimeError("cookies command unavailable"))
        pool = self._pool(channel)
        events: list[str] = []
        done: list[tuple] = []
        with tempfile.TemporaryDirectory() as folder:
            pool.backup_app_to_pc(
                ["172.30.3.42:5901"], "com.beeasy.shopee.vn", "pc-test", folder,
                on_event=lambda _key, message: events.append(message),
                on_done=lambda description, ok, failures: done.append(
                    (description, ok, failures)),
            )
            root = Path(folder) / "B42_172.30.3.42-5901" / "com.beeasy.shopee.vn"
            self.assertTrue((root / "pc-test" / "snapshot-marker.txt").exists())
            self.assertEqual(done[0][1:], (1, []))
            self.assertIn("\u0111\u01b0\u1ee3c cookie Shopee", events[0])


if __name__ == "__main__":
    unittest.main()
