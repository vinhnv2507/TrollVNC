"""Chụp ảnh, ghi hình, kịch bản — kiểm tra với server RFB giả."""

from __future__ import annotations

import asyncio
import struct
import sys
import time
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios import script                              # noqa: E402
from controlios.config import DeviceSpec, Settings         # noqa: E402
from controlios.util.png import encode_png                 # noqa: E402
from controlios.vnc.pool import DevicePool, _slug          # noqa: E402
from controlios.vnc.session import State, Tier, VncSession  # noqa: E402
from tests.fake_vnc import FakeVncServer                   # noqa: E402


class PngTest(unittest.TestCase):
    def test_encodes_a_readable_png(self) -> None:
        width, height = 3, 2
        rgb = bytes([255, 0, 0] * width * height)
        data = encode_png(rgb, width, height)

        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        # IHDR: length 13, tag, then w/h/bitdepth/colourtype
        w, h, depth, colour = struct.unpack(">IIBB", data[16:26])
        self.assertEqual((w, h, depth, colour), (width, height, 8, 2))

        # Decode IDAT back and check the scanlines round-trip.
        idat_start = data.index(b"IDAT") + 4
        idat_len = struct.unpack(">I", data[idat_start - 8:idat_start - 4])[0]
        raw = zlib.decompress(data[idat_start:idat_start + idat_len])
        self.assertEqual(len(raw), height * (1 + width * 3))
        for y in range(height):
            row = raw[y * (1 + width * 3):(y + 1) * (1 + width * 3)]
            self.assertEqual(row[0], 0, "filter byte phải là 0")
            self.assertEqual(row[1:], rgb[:width * 3])

    def test_rejects_wrong_buffer_size(self) -> None:
        with self.assertRaises(ValueError):
            encode_png(b"\x00\x00\x00", 5, 5)

    def test_slug_makes_windows_safe_names(self) -> None:
        self.assertEqual(_slug("iPhone 01 / tầng 2"), "iPhone-01-t-ng-2")
        self.assertEqual(_slug("???"), "device")


class ScriptParseTest(unittest.TestCase):
    def test_parses_every_statement(self) -> None:
        steps = script.parse(
            """
            # mở app rồi vuốt
            tap 0.5 0.85
            swipe 0.5 0.8 0.5 0.2 0.4
            text Xin chào bạn
            key Return
            wait 1.5
            shot ket-qua
            """
        )
        self.assertEqual([s.op for s in steps],
                         ["tap", "swipe", "text", "key", "wait", "shot"])
        self.assertEqual(steps[0].args, (0.5, 0.85))
        self.assertEqual(steps[1].args, (0.5, 0.8, 0.5, 0.2, 0.4, 0.0))  # hold mặc định 0
        self.assertEqual(steps[2].args, ("Xin chào bạn",))
        self.assertEqual(steps[5].args, ("ket-qua",))

    def test_swipe_accepts_an_explicit_hold(self) -> None:
        steps = script.parse("swipe 0.5 0.99 0.5 0.45 0.35 0.7")
        self.assertEqual(steps[0].args, (0.5, 0.99, 0.5, 0.45, 0.35, 0.7))

    def test_repeat_block_and_step_count(self) -> None:
        steps = script.parse(
            "repeat 3\n"
            "    tap 0.5 0.5\n"
            "    wait 0.1\n"
            "wait 1\n"
        )
        self.assertEqual([s.op for s in steps], ["repeat", "wait"])
        self.assertEqual(len(steps[0].body), 2)
        self.assertEqual(script.count_steps(steps), 3 * 2 + 1)

    def test_rejects_pixel_coordinates(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("tap 375 667")
        self.assertIn("0..1", str(ctx.exception))

    def test_reports_the_offending_line(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("tap 0.5 0.5\nwait 1\nnhay 3\n")
        self.assertEqual(ctx.exception.line_no, 3)

    def test_repeat_needs_a_body(self) -> None:
        with self.assertRaises(script.ScriptError):
            script.parse("repeat 2\ntap 0.5 0.5\n")

    def test_describe_is_human_readable(self) -> None:
        lines = script.describe(script.parse("repeat 2\n    tap 0.5 0.25\n"))
        self.assertEqual(lines, ["lặp 2 lần:", "  chạm (50%, 25%)"])


class GestureTest(unittest.TestCase):
    def test_every_default_gesture_parses(self) -> None:
        from controlios import gestures

        for name in gestures.DEFAULT_GESTURES:
            param = "3" if name == "closeall" else "Instagram"
            with self.subTest(gesture=name):
                steps = script.parse(f"{name} {param}".strip())
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].op, "macro")
                self.assertTrue(steps[0].body, f"{name} khai triển ra rỗng")
                self.assertGreater(script.count_steps(steps), 0)

    def test_openapp_puts_the_name_into_a_text_step(self) -> None:
        steps = script.parse("openapp Instagram")
        flat = _flatten(steps)
        typed = [s for s in flat if s.op == "text"]
        self.assertEqual([s.args[0] for s in typed], ["Instagram"])
        self.assertTrue([s for s in flat if s.op == "key" and s.args == ("Return",)])

    def test_openapp_keeps_multi_word_names(self) -> None:
        steps = script.parse("openapp Cài đặt")
        typed = [s for s in _flatten(steps) if s.op == "text"]
        self.assertEqual([s.args[0] for s in typed], ["Cài đặt"])

    def test_home_uses_the_hardware_button_not_a_swipe(self) -> None:
        """TrollVNC map chuột phải thành nút Home: chắc chắn hơn vuốt mò toạ độ."""

        steps = _flatten(script.parse("home"))
        buttons = [s for s in steps if s.op == "button"]
        self.assertTrue(buttons, "home phải bấm nút, không vuốt")
        self.assertEqual(buttons[0].args[0], 2, "nút Home là chuột phải (2)")
        self.assertFalse([s for s in steps if s.op == "swipe"])

    def test_switcher_is_a_double_home_press(self) -> None:
        steps = _flatten(script.parse("switcher"))
        buttons = [s for s in steps if s.op == "button"]
        self.assertEqual([b.args[0] for b in buttons], [2, 2])
        waits = [s for s in steps if s.op == "wait"]
        self.assertLess(waits[0].args[0], 0.3, "hai lần bấm phải sát nhau")

    def test_lock_uses_the_power_button(self) -> None:
        buttons = [s for s in _flatten(script.parse("lock")) if s.op == "button"]
        self.assertEqual(buttons[0].args[0], 1, "nút Power là chuột giữa (1)")

    def test_swipe_fallbacks_still_exist_and_hold(self) -> None:
        """Máy nào bấm nút không ăn thì vẫn còn đường vuốt."""

        swipes = [s for s in _flatten(script.parse("switcher_swipe")) if s.op == "swipe"]
        self.assertTrue(swipes)
        self.assertGreater(swipes[0].args[5], 0.0, "vuốt mở switcher phải có giữ")

    def test_button_accepts_explicit_coordinates(self) -> None:
        steps = script.parse("button left 0.25 0.75")
        self.assertEqual(steps[0].args, (0, 0.25, 0.75, "left"))

    def test_button_rejects_unknown_names(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("button volumeup")
        self.assertIn("home", str(ctx.exception))

    def test_describe_names_the_button(self) -> None:
        self.assertEqual(script.describe(script.parse("button power")),
                         ["nhấn nút power"])

    def test_closeall_expands_the_requested_number_of_flicks(self) -> None:
        steps = script.parse("closeall 4")
        repeats = [s for s in _flatten(steps) if s.op == "repeat"]
        self.assertTrue(repeats)
        self.assertEqual(repeats[0].args[0], 4)

    def test_macro_needing_a_parameter_says_so(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("openapp")
        self.assertIn("openapp", str(ctx.exception))

    def test_unknown_command_lists_the_gestures(self) -> None:
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("mokhoa")
        self.assertIn("openapp", str(ctx.exception))

    def test_custom_gestures_override_defaults(self) -> None:
        import json
        import tempfile
        from controlios import gestures

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gestures.json"
            path.write_text(json.dumps({"home": "key Home\nwait 0.5"}), encoding="utf-8")
            custom = gestures.load_gestures(path)
            steps = script.parse("home", custom)
            keys = [s for s in _flatten(steps) if s.op == "key"]
            self.assertEqual([s.args for s in keys], [("Home",)])

    def test_custom_gesture_cannot_shadow_a_builtin(self) -> None:
        import json
        import tempfile
        from controlios import gestures

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gestures.json"
            path.write_text(json.dumps({"tap": "wait 1"}), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                gestures.load_gestures(path)
            self.assertIn("lệnh có sẵn", str(ctx.exception))

    def test_recursive_gesture_is_caught_not_hung(self) -> None:
        custom = {"loop1": "loop2", "loop2": "loop1"}
        with self.assertRaises(script.ScriptError) as ctx:
            script.parse("loop1", custom)
        self.assertIn("lồng nhau quá sâu", str(ctx.exception))

    def test_describe_names_the_gesture(self) -> None:
        lines = script.describe(script.parse("openapp Zalo"))
        self.assertEqual(lines, ["mở app «Zalo»"])


def _flatten(steps):
    out = []
    for step in steps:
        out.append(step)
        out.extend(_flatten(step.body))
    return out


def fast_settings() -> Settings:
    return Settings(grid_fps=20.0, live_fps=40.0, stall_timeout=5.0,
                    reconnect_delay=0.2, connect_concurrency=8)


class CaptureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeVncServer()
        self.port = await self.server.start()
        self.session = VncSession(
            DeviceSpec(host="127.0.0.1", port=self.port, name="iPhone-test"),
            fast_settings(), asyncio.Semaphore(4),
            on_frame=lambda f: None, on_status=lambda k, s, d: None,
        )

    async def asyncTearDown(self) -> None:
        await self.session.stop()
        await self.server.stop()

    async def wait_online(self) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.session.state is State.ONLINE:
                return
            await asyncio.sleep(0.05)
        self.fail("phiên không lên online")

    async def test_capture_returns_full_resolution_even_when_idle(self) -> None:
        self.session.set_tier(Tier.IDLE)
        self.session.start()
        await self.wait_online()

        frame = await asyncio.wait_for(self.session.request_capture(), timeout=8)
        self.assertEqual((frame.width, frame.height),
                         (self.server.width, self.server.height))
        self.assertEqual(len(frame.data), frame.width * frame.height * 3)

    async def test_concurrent_requests_share_one_round_trip(self) -> None:
        self.session.set_tier(Tier.IDLE)
        self.session.start()
        await self.wait_online()
        await asyncio.wait_for(self.session.request_capture(), timeout=8)

        before = self.server.update_requests
        frames = await asyncio.wait_for(
            asyncio.gather(*(self.session.request_capture() for _ in range(4))),
            timeout=8,
        )
        self.assertEqual(len(frames), 4)
        self.assertEqual(self.server.update_requests - before, 1,
                         "4 lời gọi phải gộp thành 1 lượt hỏi framebuffer")

    async def test_capture_fails_loudly_when_the_phone_drops(self) -> None:
        self.session.set_tier(Tier.IDLE)
        self.session.start()
        await self.wait_online()

        future = self.session.request_capture()
        await self.server.stop()
        with self.assertRaises((ConnectionError, TimeoutError, OSError)):
            await asyncio.wait_for(future, timeout=8)


class PoolFeatureTest(unittest.TestCase):
    """Pool chạy loop riêng ở thread nền — gọi từ code đồng bộ như UI vẫn làm."""

    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.servers: list[FakeVncServer] = []

        async def boot():
            for _ in range(3):
                server = FakeVncServer()
                await server.start()
                self.servers.append(server)

        self.loop.run_until_complete(boot())
        import threading
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        self.pool = DevicePool(fast_settings(), on_frame=lambda f: None,
                               on_status=lambda k, s, d: None)
        self.pool.start()
        self.specs = [
            DeviceSpec(host="127.0.0.1", port=s.port, name=f"iPhone-{i:02d}")
            for i, s in enumerate(self.servers, start=1)
        ]
        self.pool.set_devices(self.specs)
        self.assertTrue(self.wait_until(
            lambda: self.pool.stats()["online"] == len(self.specs), 15),
            "các phiên không lên online")

    def tearDown(self) -> None:
        self.pool.stop()

        async def shutdown():
            await asyncio.gather(*(s.stop() for s in self.servers))

        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

    @staticmethod
    def wait_until(predicate, timeout: float = 10) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_batch_capture_writes_one_png_per_device(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            results: list[tuple] = []
            self.pool.capture(
                [s.key for s in self.specs], tmp,
                on_event=lambda k, p, e: results.append((k, p, e)),
            )
            self.assertTrue(self.wait_until(lambda: len(results) == 3, 15),
                            f"chỉ nhận được {results}")

            errors = [r for r in results if r[2]]
            self.assertFalse(errors, f"có lỗi khi chụp: {errors}")
            files = sorted(Path(tmp).glob("*.png"))
            self.assertEqual(len(files), 3)
            for path in files:
                self.assertGreater(path.stat().st_size, 100)
                self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertTrue(any("iPhone-01" in f.name for f in files))

    def test_recording_produces_a_frame_sequence_and_stops(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            rec_id = self.pool.start_recording([self.specs[0].key], tmp, fps=10)
            self.assertTrue(self.wait_until(
                lambda: len(list(Path(tmp).rglob("*.png"))) >= 3, 15),
                "không ghi được khung hình nào")

            self.pool.stop_recording(rec_id)
            self.assertTrue(self.wait_until(lambda: not self.pool.is_recording(), 10))

            frames = sorted(Path(tmp).rglob("*.png"))
            settled = len(frames)
            time.sleep(0.6)
            self.assertEqual(len(list(Path(tmp).rglob("*.png"))), settled,
                             "vẫn ghi tiếp sau khi đã dừng")
            self.assertIn("iPhone-01", str(frames[0]))
            self.assertTrue(frames[0].name.endswith("_000001.png"), frames[0].name)

    def test_script_runs_on_every_selected_device(self) -> None:
        import tempfile

        steps = script.parse(
            "repeat 2\n"
            "    tap 0.5 0.5\n"
            "text hi\n"
            "key Return\n"
            "shot cuoi\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            events: list[tuple] = []
            done = []
            self.pool.run_script(
                [s.key for s in self.specs], steps, tmp,
                on_event=lambda k, m: events.append((k, m)),
                on_done=lambda: done.append(True),
            )
            self.assertTrue(self.wait_until(lambda: done, 20), f"kịch bản treo: {events}")

            failures = [e for e in events if "LỖI" in e[1]]
            self.assertFalse(failures, f"kịch bản lỗi: {failures}")
            for spec, server in zip(self.specs, self.servers):
                finished = [e for e in events if e[0] == spec.key and e[1] == "xong"]
                self.assertTrue(finished, f"{spec.key} chưa chạy xong")
                # 2 lần tap = 2 press + 2 release điểm giữa màn hình
                taps = [e for e in server.pointer_events
                        if e[1:] == (server.width // 2, server.height // 2)]
                self.assertGreaterEqual(len(taps), 4, f"{spec.key}: {taps}")
                # "hi" = 2 ký tự + Return, mỗi phím có nhấn và nhả
                self.assertGreaterEqual(len(server.key_events), 6)

            shots = list(Path(tmp).glob("*.png"))
            self.assertEqual(len(shots), 3, "mỗi máy phải có 1 ảnh từ lệnh shot")
            self.assertTrue(all("cuoi" in s.name for s in shots))

    def test_home_button_reaches_the_server_as_a_right_click(self) -> None:
        """Nút Home của iOS = chuột phải theo TrollVNC, phải tới đúng nút 3."""

        import tempfile

        server = self.servers[0]
        server.pointer_events.clear()
        steps = script.parse("home")

        with tempfile.TemporaryDirectory() as tmp:
            done = []
            self.pool.run_script([self.specs[0].key], steps, tmp,
                                 on_done=lambda: done.append(True))
            self.assertTrue(self.wait_until(lambda: done, 20), "nút Home treo")

        masks = {e[0] for e in server.pointer_events if e[0]}
        self.assertEqual(masks, {1 << 2}, f"phải là chuột phải: {server.pointer_events}")

    def test_gesture_sends_a_real_press_hold_release(self) -> None:
        """Vuốt dự phòng phải là nhấn → kéo → GIỮ → nhả, đúng cử chỉ iOS."""

        import tempfile

        server = self.servers[0]
        server.pointer_events.clear()
        steps = script.parse("switcher_swipe")

        with tempfile.TemporaryDirectory() as tmp:
            done = []
            self.pool.run_script([self.specs[0].key], steps, tmp,
                                 on_done=lambda: done.append(True))
            self.assertTrue(self.wait_until(lambda: done, 20), "cử chỉ treo")

        events = server.pointer_events
        self.assertGreaterEqual(len(events), 4, events)

        pressed = [e for e in events if e[0] == 1]
        released = [e for e in events if e[0] == 0]
        self.assertTrue(pressed, "không có sự kiện nhấn")
        self.assertTrue(released, "không có sự kiện nhả")

        # Nhấn ở sát đáy màn hình, nhả ở khoảng giữa: đúng chiều vuốt lên.
        self.assertGreater(pressed[0][2], server.height * 0.9, f"điểm nhấn: {pressed[0]}")
        self.assertLess(released[-1][2], server.height * 0.6, f"điểm nhả: {released[-1]}")
        # Nút phải còn giữ suốt quá trình kéo.
        self.assertGreaterEqual(len(pressed), 3, "chuột phải giữ trong lúc kéo")

    def test_openapp_types_the_name_on_the_device(self) -> None:
        import tempfile

        server = self.servers[0]
        server.key_events.clear()
        steps = script.parse("openapp Zalo")

        with tempfile.TemporaryDirectory() as tmp:
            done = []
            self.pool.run_script([self.specs[0].key], steps, tmp,
                                 on_done=lambda: done.append(True))
            self.assertTrue(self.wait_until(lambda: done, 25), "openapp treo")

        # Mỗi ký tự có nhấn (down=1) rồi nhả; ghép lại phải ra đúng "Zalo".
        typed = "".join(chr(sym) for down, sym in server.key_events
                        if down == 1 and 32 <= sym < 127)
        self.assertIn("Zalo", typed, f"đã gõ: {typed!r}")
        self.assertIn(0xFF0D, [sym for down, sym in server.key_events if down == 1],
                      "thiếu phím Return để mở kết quả đầu tiên")

    def test_script_can_be_cancelled(self) -> None:
        import tempfile

        steps = script.parse("repeat 40\n    wait 0.5\n")
        with tempfile.TemporaryDirectory() as tmp:
            events: list[tuple] = []
            done = []
            self.pool.run_script(
                [s.key for s in self.specs], steps, tmp,
                on_event=lambda k, m: events.append((k, m)),
                on_done=lambda: done.append(True),
            )
            self.assertTrue(self.wait_until(lambda: len(events) >= 3, 10))
            self.pool.cancel_script()
            self.assertTrue(self.wait_until(lambda: done, 10), "huỷ không dừng được")
            self.assertTrue([e for e in events if e[1] == "đã huỷ"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
