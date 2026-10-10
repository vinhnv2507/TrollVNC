"""Back navigation stays on the requested phone and scales to its screen."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from controlios import script
from controlios.control_channel import ControlChannel, ControlError, NotPatchedError
from controlios.config import DeviceSpec, Registry, Settings
from controlios.ui.app import MainWindow
from controlios.ui.branding import app_icon
from controlios.vnc.session import VncSession, State, Tier
from tests.fake_vnc import FakeVncServer

app = QApplication.instance() or QApplication([])


class BackGestureTest(unittest.IsolatedAsyncioTestCase):
    async def test_safari_sends_one_history_shortcut_without_swiping(self):
        session, server, timestamps = await self.connect(375, 667)
        control = AsyncMock()
        control.frontmost_app.return_value = "com.apple.mobilesafari"
        await script.run_on_session(session, script.parse("back"), lambda *args: None,
                                    control=control)
        await asyncio.sleep(.05)
        self.assertEqual(server.pointer_events, [])
        self.assertEqual(server.key_events, [(1, 65515), (1, 91), (0, 91), (0, 65515)])
        control.frontmost_app.assert_awaited_once()

    async def test_settings_sends_one_navigation_shortcut(self):
        session, server, timestamps = await self.connect(375, 667)
        control = AsyncMock()
        control.frontmost_app.return_value = "com.apple.Preferences"
        await script.run_on_session(session, script.parse("back"), lambda *args: None,
                                    control=control)
        await asyncio.sleep(.05)
        self.assertEqual(server.pointer_events, [])
        self.assertEqual(server.key_events, [(1, 65515), (1, 91), (0, 91), (0, 65515)])
        control.navigate_back.assert_not_awaited()

    async def test_shopee_uses_native_back_without_a_second_action(self):
        session, server, timestamps = await self.connect(375, 667)
        control = AsyncMock()
        control.frontmost_app.return_value = "com.beeasy.shopee.vn"
        control.navigate_back.return_value = True
        await script.run_on_session(session, script.parse("back"), lambda *args: None, control=control)
        control.navigate_back.assert_awaited_once()
        self.assertEqual(server.pointer_events, [])
        self.assertEqual(server.key_events, [])

    async def test_system_screen_keeps_existing_edge_navigation(self):
        session, server, timestamps = await self.connect(375, 667)
        control = AsyncMock()
        control.frontmost_app.return_value = None
        await script.run_on_session(session, script.parse("back"), lambda *args: None, control=control)
        self.assertGreater(len(server.pointer_events), 20)
        self.assertEqual(server.key_events, [])
        control.navigate_back.assert_not_awaited()

    async def test_no_arrow_does_not_fall_back_to_a_guessed_tap(self):
        session, server, timestamps = await self.connect(375, 667)
        control = AsyncMock()
        control.frontmost_app.return_value = "com.beeasy.shopee.vn"
        control.navigate_back.return_value = False
        with self.assertRaisesRegex(ConnectionError, "Không tìm thấy"):
            await script.run_on_session(session, script.parse("back"), lambda *args: None, control=control)
        self.assertEqual(server.pointer_events, [])
        self.assertEqual(server.key_events, [])

    async def test_native_back_requires_update_and_never_retries_a_tap(self):
        channel = ControlChannel("127.0.0.1", 46752, "test")
        channel.command = AsyncMock(side_effect=NotPatchedError("old"))
        with self.assertRaisesRegex(ControlError, "4.34"):
            await channel.navigate_back()
        channel.command.assert_awaited_once()

    async def test_native_back_response_and_busy_error(self):
        channel = ControlChannel("127.0.0.1", 46752, "test")
        channel.command = AsyncMock(side_effect=["OK tapped\n", "OK none\n", ControlError("ERR BackBusy")])
        self.assertTrue(await channel.navigate_back())
        self.assertFalse(await channel.navigate_back())
        with self.assertRaisesRegex(ControlError, "AutoClickJS"):
            await channel.navigate_back()

    async def test_cancel_during_app_query_does_not_send_back(self):
        session, server, timestamps = await self.connect(375, 667)
        cancel = asyncio.Event()
        async def frontmost():
            cancel.set()
            return "com.apple.mobilesafari"
        control = AsyncMock()
        control.frontmost_app.side_effect = frontmost
        with self.assertRaises(asyncio.CancelledError):
            await script.run_on_session(session, script.parse("back"), lambda *args: None,
                                        cancel=cancel, control=control)
        self.assertEqual(server.key_events, [])
        self.assertEqual(server.pointer_events, [])

    async def connect(self, width, height):
        timestamps = []
        class TimedEvents(list):
            def append(self, value):
                timestamps.append(time.monotonic())
                super().append(value)
        server = FakeVncServer(width=width, height=height, pointer_events=TimedEvents())
        port = await server.start()
        self.addAsyncCleanup(server.stop)
        session = VncSession(DeviceSpec("127.0.0.1", port=port), Settings(),
                             asyncio.Semaphore(1), lambda frame: None, lambda *args: None)
        self.addAsyncCleanup(session.stop)
        session.set_tier(Tier.GRID)
        session.start()
        deadline = time.monotonic() + 5
        while session.state is not State.ONLINE and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        self.assertIs(session.state, State.ONLINE)
        server.pointer_events.clear(); timestamps.clear()
        return session, server, timestamps

    async def test_edge_swipe_scales_to_portrait_landscape_and_small_streams(self):
        for width, height in ((375, 667), (667, 375), (131, 233)):
            with self.subTest(size=(width, height)):
                session, server, timestamps = await self.connect(width, height)
                await script.run_on_session(session, script.parse("back"), lambda *args: None)
                await asyncio.sleep(.05)
                events = server.pointer_events
                self.assertGreater(len(events), 20)
                self.assertEqual(events[0][0], 1)
                self.assertEqual(events[-1][0], 0)
                self.assertTrue(all(event[0] == 1 for event in events[:-1]))
                _, x1, y1 = events[0]
                _, x2, y2 = events[-1]
                self.assertGreater(x1, 0, "Start inside the edge, not at a rounded zero")
                self.assertLessEqual(x1, width * .01)
                self.assertGreater(x2, width * .8)
                self.assertLess(x2, width)
                self.assertEqual(y1, y2)
                self.assertLess(abs(y1 - height / 2), 1)
                self.assertGreater(timestamps[1] - timestamps[0], .04,
                                   "Touch-began must reach the phone before moving")
                self.assertTrue(all(0 <= b[1]-a[1] <= width*.05
                                    for a, b in zip(events, events[1:])))
                self.assertEqual(server.key_events, [])

    async def test_stop_releases_the_finger_during_initial_edge_hold(self):
        session, server, timestamps = await self.connect(131, 233)
        task = asyncio.create_task(session.edge_back())
        await asyncio.sleep(.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(.05)
        self.assertGreaterEqual(len(server.pointer_events), 2)
        self.assertEqual(server.pointer_events[0][0], 1)
        self.assertEqual(server.pointer_events[-1][0], 0)
        self.assertEqual(session._client.mouse.buttons, 0)


class BackWindowTest(unittest.TestCase):
    def test_old_exported_default_updates_but_custom_back_is_preserved(self):
        from controlios.gestures import load_gestures
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gestures.json"
            previous = "swipe 0.003 0.5 0.88 0.5 0.35\nwait 0.25"
            path.write_text(json.dumps({"back": previous, "home": "wait 1"}), encoding="utf-8")
            before = path.read_bytes()
            loaded = load_gestures(path)
            self.assertIn("navigateback", loaded["back"])
            self.assertEqual(loaded["home"], "wait 1")
            self.assertEqual(path.read_bytes(), before)
            custom = "swipe 0.01 0.6 0.8 0.6 0.7"
            path.write_text(json.dumps({"back": custom}), encoding="utf-8")
            self.assertEqual(load_gestures(path)["back"], custom)

    def test_back_buttons_route_main_selection_and_each_large_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.json"
            registry = Registry(devices=[DeviceSpec("127.0.0.1", port=port) for port in (5901, 5902)])
            registry.save(path)
            with patch("controlios.ui.app.DevicePool"):
                window = MainWindow(path)
                window.pool.updating_ios = set()
                keys = [d.key for d in registry.devices]
                try:
                    self.assertFalse(app_icon().isNull())
                    self.assertFalse(window.windowIcon().isNull())
                    with patch.object(window, "start_script") as send:
                        window.grid._apply_selection([keys[0]])
                        window.device_gesture_buttons["back"].click()
                        self.assertEqual(send.call_args.args[1], [keys[0]])
                        self.assertEqual(send.call_args.args[0][0].args[0], "back")
                        window._open_multi_detail(keys)
                        window.multi_detail_window.panes[keys[1]].buttons["back"].click()
                        self.assertEqual(send.call_args.args[1], [keys[1]])
                        self.assertEqual(send.call_args.args[0][0].args[0], "back")
                        self.assertEqual(send.call_count, 2)
                finally:
                    window.close()
