import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from controlios.config import DeviceSpec, Settings
from controlios.control_channel import ControlChannel, ControlError, LicenseStatus, parse_license_status
from controlios.ui.activation import ActivationDialog
from controlios.vnc.pool import DevicePool


class ActivationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reply = b"OK valid exp=2000000000 udid=phone-133\n"
        self.headers = []
        self.bodies = []
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.channel = ControlChannel("127.0.0.1", self.server.sockets[0].getsockname()[1], "manager-token", timeout=2)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, reader, writer):
        try:
            header = await reader.readline()
            self.headers.append(header)
            if not header.startswith(b"auth manager-token "):
                writer.write(b"ERR Unauthorized\n")
            elif b"activate " in header:
                count = int(header.strip().split()[-1])
                self.bodies.append(await reader.readexactly(count))
                # Send a fragmented response as a real LAN connection may do.
                writer.write(self.reply[:5])
                await writer.drain()
                await asyncio.sleep(.001)
                writer.write(self.reply[5:])
            else:
                writer.write(b"OK invalid udid=phone-133 remaining=0\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_expired_status_without_viewer(self):
        status = await self.channel.license_status()
        self.assertEqual(status.state, "invalid")
        self.assertEqual(status.udid, "phone-133")

    async def test_framed_key_exceeds_old_command_line_limit(self):
        key = "payload." + "s" * 2500
        status = await self.channel.activate_license(" \n" + key + "\n ")
        self.assertEqual(status.state, "valid")
        self.assertEqual(self.bodies, [key.encode()])
        self.assertLess(len(self.headers[0]), 100)
        self.assertNotIn(key.encode(), self.headers[0])

    async def test_refusals_are_clear_and_do_not_expose_key(self):
        for reason in ["LicenseSignature", "LicenseDeviceMismatch", "LicenseExpired",
                       "LicenseTokenMismatch", "LicenseIncomplete", "LicenseSaveFailed", "Unknown", "NotActivated"]:
            with self.subTest(reason=reason):
                self.reply = f"ERR {reason}\n".encode()
                with self.assertRaises(ControlError) as caught:
                    await self.channel.activate_license("private-customer-key")
                self.assertNotIn("private-customer-key", str(caught.exception))
                self.assertNotEqual(str(caught.exception), "ERR " + reason)

    async def test_no_success_for_trial_or_invalid_response(self):
        for reply in [b"OK trial udid=phone-133 remaining=5\n",b"OK invalid udid=phone-133 remaining=0\n",b"OK\n"]:
            self.reply = reply
            with self.assertRaises(ControlError):
                await self.channel.activate_license("a.b")

    async def test_invalid_size_does_not_open_connection(self):
        for key in [" ","a" * 16385]:
            with self.assertRaises(ControlError):
                await self.channel.activate_license(key)
        self.assertEqual(self.headers, [])

    async def test_wrong_auth_cannot_send_key(self):
        self.channel.token = "wrong-token"
        with self.assertRaises(ControlError):
            await self.channel.activate_license("a.b")
        self.assertEqual(self.bodies, [])


class LicenseStatusTests(unittest.TestCase):
    def test_finite_and_forever_and_trial(self):
        self.assertEqual(parse_license_status("OK valid exp=0 udid=u").expires_text, "Vĩnh viễn")
        self.assertIn("Đã kích hoạt", parse_license_status("OK valid exp=2000000000 udid=u").description)
        self.assertIn("1:01", parse_license_status("OK trial remaining=61 udid=u").description)
        for bad in ["OK bad", "OK trial remaining=-1", "OK valid exp=x"]:
            with self.assertRaises(ControlError):
                parse_license_status(bad)


class ActivationPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_only_on_confirmed_success_without_existing_session(self):
        pool = DevicePool(Settings(), lambda *args: None, lambda *args: None)
        pool._loop = asyncio.get_running_loop()
        pool._reconnect_now = Mock()
        key = "172.30.2.133:5901"
        status = LicenseStatus("valid", "u", 0)
        class Channel:
            async def activate_license(self, license_key):
                if license_key == "bad": raise ControlError("Sai key")
                return status
        pool._channel = lambda _key: Channel()
        for license_key in ["bad", "good"]:
            done = asyncio.Event()
            results = []
            def callback(*args):
                results.append(args)
                done.set()
            pool.activate_license(key, license_key, callback)
            await asyncio.wait_for(done.wait(), 3)
            if license_key == "bad":
                pool._reconnect_now.assert_not_called()
                self.assertIsNone(results[0][1])
            else:
                pool._reconnect_now.assert_called_once_with([key])
                self.assertEqual(results[0][1], status)


app = QApplication.instance() or QApplication([])
class ActivationUITests(unittest.TestCase):
    def setUp(self):
        self.pool = SimpleNamespace(check_license=Mock(),activate_license=Mock())
        self.devices = [DeviceSpec("172.30.2.133",name="6s1"),DeviceSpec("172.30.2.63",name="6s2")]
        self.dialog = ActivationDialog(self.pool,self.devices)
        for call in self.pool.check_license.call_args_list:
            key, callback = call.args
            callback(key,LicenseStatus("invalid","udid-"+key),"")

    def tearDown(self):
        self.dialog.pending.clear()
        self.dialog.close()
        self.dialog.deleteLater()
        app.processEvents()

    def test_expired_device_can_activate_and_success_clears_key(self):
        key = self.devices[0].key
        self.dialog.editors[key].setPlainText("customer-key")
        self.dialog.activate()
        self.pool.activate_license.assert_called_once()
        args = self.pool.activate_license.call_args.args
        self.assertEqual(args[:2],(key,"customer-key"))
        args[2](key,LicenseStatus("valid","udid",2000000000),"")
        self.assertEqual(self.dialog.editors[key].toPlainText(),"")
        self.assertEqual(self.dialog.table.item(0,2).text(),"Đã kích hoạt")
        self.assertTrue(self.dialog.activate_button.isEnabled())

    def test_failure_preserves_key_and_copy_udid_works(self):
        key = self.devices[0].key
        self.dialog._copy_udid(0,1)
        self.assertEqual(app.clipboard().text(),"udid-"+key)
        self.dialog.editors[key].setPlainText("bad-key")
        self.dialog.activate()
        args = self.pool.activate_license.call_args.args
        args[2](key,None,"Key đã hết hạn")
        self.assertEqual(self.dialog.editors[key].toPlainText(),"bad-key")
        self.assertIn("hết hạn",self.dialog.table.item(0,2).text())

    def test_multi_device_results_and_close_while_pending(self):
        for d in self.devices: self.dialog.editors[d.key].setPlainText("key-"+d.host)
        self.dialog.activate()
        self.dialog.show()
        self.dialog.close()
        self.assertTrue(self.dialog.pending)
        self.assertFalse(self.dialog.isVisible())
        for call in self.pool.activate_license.call_args_list:
            key, _license, callback = call.args
            callback(key,LicenseStatus("valid","udid",0),"")
        self.assertFalse(self.dialog.pending)
        self.assertTrue(self.dialog.activate_button.isEnabled())

class ActivationEntryTests(unittest.TestCase):
    def test_offline_grid_and_context_targets_open_their_registered_devices(self):
        from controlios.ui.app import MainWindow
        devices = [DeviceSpec("172.30.2.133"), DeviceSpec("172.30.2.63")]
        owner = SimpleNamespace(pool=Mock(), registry=SimpleNamespace(devices=devices),
                                grid=SimpleNamespace(selection={devices[0].key}),
                                detail=SimpleNamespace(key=None), _refresh_device_names=Mock())
        with patch("controlios.ui.activation.ActivationDialog") as dialog:
            MainWindow._activate_ios(owner)
            self.assertEqual(dialog.call_args.args[1], [devices[0]])
            owner.activation_dialog = None
            MainWindow._activate_ios(owner, [devices[1].key])
            self.assertEqual(dialog.call_args.args[1], [devices[1]])

    def test_pending_hidden_dialog_is_reused(self):
        from controlios.ui.app import MainWindow
        existing = Mock(pending={"phone"})
        existing.isVisible.return_value = False
        owner = SimpleNamespace(activation_dialog=existing)
        MainWindow._activate_ios(owner)
        existing.showNormal.assert_called_once()

if __name__ == "__main__":
    unittest.main()
