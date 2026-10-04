"""Proxy isolation, partial API results, persistence and Shopee table lifecycle."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication

from controlios.shopee import (Account, AccountStore, ShopeeClient, ShopeeError,
                               normalize_cookie, normalize_proxy, parse_order, parse_voucher, proxy_label)
from controlios.ui.shopee_panel import CheckWorker, ShopeeDialog

app = QApplication.instance() or QApplication([])


class ProxyTest(unittest.TestCase):
    def test_supported_formats_and_masking(self):
        self.assertEqual(normalize_proxy("localhost:8080:user:p@ss:word"), "http://user:p%40ss%3Aword@localhost:8080")
        self.assertEqual(normalize_proxy("socks5://user:pass@localhost:1080"), "socks5h://user:pass@localhost:1080")
        self.assertEqual(proxy_label("localhost:80:secret:password"), "http://localhost:80")
        for value in ("", "localhost", "localhost:0", "http://localhost:99/path", "ftp://localhost:90"):
            with self.subTest(value=value), self.assertRaises(ShopeeError):
                normalize_proxy(value)

    def test_missing_proxy_never_opens_session(self):
        with patch("controlios.shopee.requests.Session") as session:
            with self.assertRaises(ShopeeError):
                ShopeeClient("SPC_ST=synthetic", "")
            session.assert_not_called()

    def test_real_connect_requests_reach_only_assigned_proxy(self):
        servers, threads = [], []
        class Proxy(BaseHTTPRequestHandler):
            def do_CONNECT(self):
                self.server.seen.append((self.path, self.headers.get("Proxy-Authorization")))
                self.send_error(502)
            def log_message(self, *args):
                pass
        try:
            for _ in range(2):
                server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
                server.seen = []
                servers.append(server)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                threads.append(thread)
            for index, server in enumerate(servers):
                client = ShopeeClient(f"SPC_ST=synthetic-{index}",
                                      f"127.0.0.1:{server.server_port}:user{index}:pass{index}")
                try:
                    with self.assertRaises(ShopeeError) as caught:
                        client.request("/api/v4/test")
                    self.assertNotIn(f"pass{index}", str(caught.exception))
                    self.assertNotIn("SPC_ST", str(caught.exception))
                finally:
                    client.close()
            self.assertEqual([len(s.seen) for s in servers], [1, 1])
            self.assertEqual(servers[0].seen[0][0], "shopee.vn:443")
            self.assertNotEqual(servers[0].seen[0][1], servers[1].seen[0][1])
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(2)

    def test_explicit_proxies_headers_and_redirect_disabled(self):
        session = Mock()
        session.request.return_value = Mock(status_code=200)
        session.request.return_value.iter_content.return_value = [json.dumps({"error": 0, "data": {"test": 1}}).encode()]
        client = ShopeeClient("SPC_ST=synthetic; csrftoken=csrf", "localhost:8080", session=session)
        self.assertEqual(client.request("/api/v4/test", {}), {"test": 1})
        self.assertFalse(session.trust_env)
        args, kwargs = session.request.call_args
        self.assertEqual(args[:2], ("POST", "https://shopee.vn/api/v4/test"))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["proxies"], {"http": "http://localhost:8080", "https": "http://localhost:8080"})
        self.assertEqual(kwargs["headers"]["x-csrftoken"], "csrf")

    def test_api_block_is_error_and_response_is_closed(self):
        session = Mock()
        session.request.return_value = Mock(status_code=200)
        session.request.return_value.iter_content.return_value = [json.dumps({"error": 90309999, "data": None}).encode()]
        client = ShopeeClient("SPC_ST=test", "localhost:8080", session=session)
        with self.assertRaisesRegex(ShopeeError, "90309999"):
            client.request("/api/v4/test")
        session.request.return_value.close.assert_called_once()


class StoreTest(unittest.TestCase):
    def test_cookie_validation(self):
        self.assertEqual(normalize_cookie("Cookie: SPC_ST=token; foo=bar"), "SPC_ST=token; foo=bar")
        self.assertEqual(normalize_cookie("token"), "SPC_ST=token")
        for value in ("", "foo=bar", "SPC_ST=", "SPC_ST=a\r\nx-header=b"):
            with self.subTest(value=value), self.assertRaises(ShopeeError):
                normalize_cookie(value)

    def test_device_recapture_preserves_proxy_and_clears_stale_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp) / "accounts.json")
            account = store.upsert("SPC_ST=old", "Phone", "localhost:99", "phone|shopee")
            account.result = {"status": "old", "vouchers": [{"code": "old"}]}
            updated = store.upsert("SPC_ST=new", "Phone", source="phone|shopee")
            self.assertIs(account, updated)
            self.assertEqual(account.proxy, "http://localhost:99")
            self.assertEqual(account.result, {})
            store.save()
            restored = AccountStore(store.path)
            self.assertEqual(restored.accounts, store.accounts)
            self.assertNotIn("new", repr(account))

    def test_same_cookie_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp) / "accounts.json")
            store.upsert("SPC_ST=token")
            store.upsert("SPC_ST=token; csrftoken=csrf", source="phone|shopee")
            self.assertEqual(len(store.accounts), 1)

    def test_corrupt_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text('{"accounts": [{"cookie": 1}]}', encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaises(ShopeeError):
                AccountStore(path)
            self.assertEqual(path.read_bytes(), before)


class ApiTest(unittest.TestCase):
    def client(self):
        return ShopeeClient("SPC_ST=synthetic", "localhost:99", session=Mock())

    def test_voucher_money_expiry_and_percentage(self):
        row = parse_voucher({"voucher": {"voucher_code": "SALE", "discount_percentage": 15,
                                         "min_spend": 50000 * 100000, "discount_cap": 20000 * 100000,
                                         "end_time": 1791000000}})
        self.assertEqual(row["discount"], "15%")
        self.assertEqual(row["min_spend"], "50.000đ")
        self.assertEqual(row["cap"], "20.000đ")
        self.assertTrue(row["expires"])
        self.assertEqual(parse_voucher({"code": "TINY", "discount_value": 50000})["discount"], "0đ")

    def test_voucher_partial_error_preserves_pages(self):
        client = self.client()
        client.request = Mock(side_effect=[{"user_voucher_list": [{"code": "A"}], "next": "cursor"},
                                           ShopeeError("HTTP 403")])
        rows, error = client.vouchers()
        self.assertEqual([r["code"] for r in rows], ["A"])
        self.assertIn("403", error)
        self.assertEqual(client.request.call_args[0][1]["cursor"], "cursor")

    def test_voucher_empty_different_from_invalid_payload(self):
        client = self.client()
        client.request = Mock(return_value={"user_voucher_list": []})
        self.assertEqual(client.vouchers(), ([], ""))
        client.request = Mock(return_value={})
        self.assertTrue(client.vouchers()[1])
        client.request = Mock(return_value={"user_voucher_list": [{"new_unknown_schema": 1}]})
        self.assertTrue(client.vouchers()[1])

    def test_detail_list_and_pagination(self):
        client = self.client()
        first = [{"order_data": {"order_id": 100000000 + index},
                  "tracking_number": f"SPX1234567890{index:02}", "item_name": "Product"} for index in range(20)]
        last = [{"order_id": 200000000, "tracking_number": "SPX123456789099", "item_name": "Last"}]
        client.request = Mock(side_effect=[{"details_list": first}, {"details_list": last}])
        rows, warning = client.orders()
        self.assertEqual(len(rows), 21)
        self.assertFalse(warning)
        self.assertIn("offset=20", client.request.call_args.args[0])

    def test_order_wrapper_keeps_shipping_and_all_products(self):
        data = {"order_data": {"order_id": 123456789, "order_sn": "261004ABCDEF"},
                "shipping": {"tracking_number": "SPX123456789012", "status_text": "Đang giao"},
                "items": [{"item_name": "Product A", "item_id": 12, "shop_id": 34}, {"item_name": "Product B"}]}
        row = parse_order(data)
        self.assertEqual(row["order_id"], "123456789")
        self.assertEqual(row["tracking"], "SPX123456789012")
        self.assertEqual(row["products"], "Product A\nProduct B")
        self.assertEqual(row["links"], "https://shopee.vn/product/34/12")
        client = self.client()
        client.request = Mock(return_value={"order_or_checkout_list": [data]})
        rows, warning = client.orders()
        self.assertEqual(rows, [row])
        self.assertFalse(warning)

    def test_blocked_order_api_retains_notifications_and_vouchers(self):
        client = self.client()
        def request(path, payload=None):
            if "voucher_wallet" in path:
                return {"user_voucher_list": [{"code": "SALE"}]}
            if "notification" in path:
                return {"actions": [{"title": "Đang giao SPX123456789012", "action_redirect_url": "https://shopee.vn/?orderId=123456789"}]}
            raise ShopeeError("Shopee yêu cầu xác minh (90309999)")
        client.request = request
        result = client.check()
        self.assertEqual(result["status"], "Có cảnh báo")
        self.assertEqual(result["vouchers"][0]["code"], "SALE")
        self.assertEqual(result["orders"][0]["tracking"], "SPX123456789012")
        self.assertIn("90309999", result["order_error"])
        self.assertFalse(result["voucher_error"])


class TableTest(unittest.TestCase):
    def test_close_waits_for_worker_and_missing_proxy_is_visible(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp) / "accounts.json")
            store.upsert("SPC_ST=synthetic")
            dialog = ShopeeDialog(store)
            try:
                dialog.start_checks(store.accounts)
                deadline = time.monotonic() + 3
                while dialog.worker is not None and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertIsNone(dialog.worker)
                self.assertIn("Chưa gán proxy", store.accounts[0].result["status"])
                self.assertIn("Chưa gán proxy", dialog.table.item(0, 3).text())
                self.assertTrue(dialog.shutdown())
            finally:
                if dialog.worker:
                    dialog.worker.requestInterruption()
                    dialog.worker.wait(3000)
                    app.processEvents()
                dialog.close()

    def test_selection_copy_and_stale_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp) / "accounts.json")
            a = store.upsert("SPC_ST=synthetic-A", "A", "localhost:99")
            b = store.upsert("SPC_ST=synthetic-B", "B", "localhost:100")
            dialog = ShopeeDialog(store)
            try:
                dialog.table.selectionModel().select(dialog.table.model().index(1, 0),
                                                       QItemSelectionModel.Select | QItemSelectionModel.Rows)
                dialog._refresh()
                self.assertEqual(len(dialog._selected()), 2)
                dialog._copy_cookie()
                self.assertIn("SPC_ST=synthetic-A", app.clipboard().text())
                dialog._completed, dialog._total = 0, 1
                fingerprint = a.fingerprint()
                a.cookie = "SPC_ST=changed"
                dialog._checked(a.id, fingerprint, {"status": "stale"})
                self.assertEqual(a.result, {})
                dialog._checked(a.id, a.fingerprint(), {"status": "ok", "orders": [], "vouchers": [{"code": "SALE"}]})
                self.assertEqual(dialog.vouchers.item(0, 0).text(), "SALE")
                dialog.vouchers.selectRow(0)
                dialog.vouchers._copy()
                self.assertTrue(app.clipboard().text().startswith("SALE"))
                self.assertEqual(AccountStore(store.path).accounts[0].result["status"], "ok")
            finally:
                dialog.close()

    def test_worker_uses_independent_account_clients_and_cancel(self):
        accounts = [Account("A", "SPC_ST=A", "localhost:99"), Account("B", "SPC_ST=B", "localhost:100")]
        calls = []
        class Client:
            def __init__(self, cookie, proxy, **kwargs):
                calls.append((cookie, proxy))
            def check(self):
                return {"status": "ok"}
            def close(self):
                pass
        worker = CheckWorker(accounts)
        with patch("controlios.ui.shopee_panel.ShopeeClient", Client):
            worker.start()
            self.assertTrue(worker.wait(2000))
        self.assertCountEqual(calls, [(a.cookie, a.proxy) for a in accounts])
        calls.clear()
        worker = CheckWorker(accounts)
        # Request interruption while worker is live and before scheduling work.
        with patch.object(worker, "isInterruptionRequested", return_value=True), patch("controlios.ui.shopee_panel.ShopeeClient", Client):
            worker.run()
        self.assertEqual(calls, [])

    def test_main_window_migrates_once_and_recaptures(self):
        from controlios.ui.app import MainWindow
        from controlios.config import Registry
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("controlios.ui.app.COOKIE_STORE_DIR", root / "cookies"), patch("controlios.ui.app.SHOPEE_STORE_PATH", root / "accounts.json"), patch("controlios.ui.app.DevicePool"):
                window = MainWindow(root / "devices.json")
                try:
                    window._cookie_headers[("phone", "com.shopee.vn")] = "SPC_ST=old"
                    store = window._get_shopee_store()
                    self.assertEqual(len(store.accounts), 1)
                    store.accounts[0].proxy = "http://localhost:99"
                    store.save()
                    window._remember_cookie("phone", "com.shopee.vn", "SPC_ST=new")
                    self.assertEqual(len(store.accounts), 1)
                    self.assertEqual(store.accounts[0].cookie, "SPC_ST=new")
                    self.assertEqual(store.accounts[0].proxy, "http://localhost:99")
                    store.accounts.clear()
                    store.save()
                    window._shopee_store = None
                    self.assertEqual(window._get_shopee_store().accounts, [])
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main()
