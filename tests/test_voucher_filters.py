"""Numeric voucher filtering works with saved results and per-account views."""
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from controlios.shopee import AccountStore
from controlios.ui.shopee_panel import ShopeeDialog, VoucherList, _voucher_number

app = QApplication.instance() or QApplication([])

ROWS = [
    {"code": "P9", "discount": "9%", "cap": "500.000đ"},
    {"code": "M100", "discount": "100.000đ"},
    {"code": "P30", "discount": "30%", "cap": "50.000đ"},
    {"code": "M9", "discount": "9.000đ"},
    {"code": "SHIP", "discount": "Miễn phí vận chuyển"},
    {"code": "P15", "discount": "15,5%", "cap": "100.000đ"},
    {"code": "M20", "discount": "20.000đ"},
    {"code": "M0", "discount": "0đ"},
    {"code": "UNKNOWN", "discount": ""},
]

class VoucherFilterTest(unittest.TestCase):
    def setUp(self):
        self.view = VoucherList()
        self.rows = copy.deepcopy(ROWS)
        self.view.load_rows(self.rows)
        self.addCleanup(self.view.close)

    def codes(self, view=None):
        table = (view or self.view).table
        return [table.item(row, 0).text() for row in range(table.rowCount())]

    def choose(self, box, value):
        index = box.findData(value)
        self.assertGreaterEqual(index, 0)
        box.setCurrentIndex(index)

    def test_money_sort_is_numeric_and_missing_values_always_last(self):
        self.choose(self.view.sort, "amount_desc")
        self.assertEqual(self.codes()[:4], ["M100", "M20", "M9", "M0"])
        self.choose(self.view.sort, "amount_asc")
        self.assertEqual(self.codes()[:4], ["M0", "M9", "M20", "M100"])
        self.assertEqual(self.codes()[4:], ["P9", "P30", "SHIP", "P15", "UNKNOWN"])
        self.assertEqual(self.rows, ROWS)

    def test_percent_sort_and_range_do_not_compare_with_money(self):
        self.choose(self.view.kind, "percentage")
        self.choose(self.view.sort, "percentage_desc")
        self.assertEqual(self.codes(), ["P30", "P15", "P9"])
        self.view.minimum.setValue(15.5)
        self.view.maximum.setValue(30)
        self.assertEqual(self.codes(), ["P30", "P15"])
        self.choose(self.view.sort, "percentage_asc")
        self.assertEqual(self.codes(), ["P15", "P30"])
        self.assertIn("2 / 9", self.view.count.text())

    def test_money_range_unlimited_maximum_invalid_range_and_reset(self):
        self.choose(self.view.kind, "amount")
        self.view.minimum.setValue(9000)
        self.view.maximum.setValue(20000)
        self.assertEqual(self.codes(), ["M9", "M20"])
        self.view.maximum.setValue(0)
        self.assertEqual(self.codes(), ["M100", "M9", "M20"])
        self.view.maximum.setValue(8000)
        self.assertEqual(self.codes(), [])
        self.assertIn("lớn hơn", self.view.count.text())
        self.view.reset()
        self.assertEqual(self.codes(), [r["code"] for r in ROWS])
        self.assertFalse(self.view.minimum.isEnabled())

    def test_limits_are_independent_between_money_and_percent(self):
        self.choose(self.view.kind, "amount")
        self.view.minimum.setValue(20000)
        self.choose(self.view.kind, "percentage")
        self.assertEqual(self.view.minimum.value(), 0)
        self.view.minimum.setValue(10)
        self.choose(self.view.kind, "amount")
        self.assertEqual(self.view.minimum.value(), 20000)
        self.assertEqual(self.codes(), ["M100", "M20"])

    def test_cap_sort_and_copy_keep_the_whole_voucher_row(self):
        self.choose(self.view.sort, "cap_desc")
        self.assertEqual(self.codes()[:3], ["P9", "P15", "P30"])
        self.choose(self.view.sort, "cap_asc")
        self.assertEqual(self.codes()[:3], ["P30", "P15", "P9"])
        self.view.table.selectRow(0)
        self.view.table._copy()
        self.assertEqual(app.clipboard().text().split("\t")[:5], ["P30", "", "", "30%", "50.000đ"])

    def test_unknown_shipping_and_zero_remain_distinct(self):
        self.choose(self.view.kind, "other")
        self.assertEqual(self.codes(), ["SHIP", "UNKNOWN"])
        self.choose(self.view.kind, "amount")
        self.assertIn("M0", self.codes())
        for value in ["NaN%", "Infinity%", "-5%", "101%", "NaNđ", "5.5đ", "", "Free"]:
            with self.subTest(value=value):
                self.assertIsNone(_voucher_number(value))
                self.assertIsNone(_voucher_number(value, percentage=True))

    def test_equal_discounts_preserve_source_order(self):
        self.view.load_rows([{"code": "FIRST", "discount": "10%"},
                             {"code": "SECOND", "discount": "10%"}])
        self.choose(self.view.sort, "percentage_desc")
        self.assertEqual(self.codes(), ["FIRST", "SECOND"])

    def test_tab_popup_refresh_and_account_switch_keep_filter_and_correct_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp)/"accounts.json")
            a = store.upsert("SPC_ST=synthetic-filter-A", "A")
            b = store.upsert("SPC_ST=synthetic-filter-B", "B")
            a.result = {"vouchers": copy.deepcopy(ROWS), "voucher_error": "partial"}
            b.result = {"vouchers": [{"code": "B50", "discount": "50.000đ"}]}
            original = copy.deepcopy(a.result)
            dialog = ShopeeDialog(store)
            try:
                dialog.table.selectRow(0)
                self.choose(dialog.voucher_list.kind, "amount")
                dialog.voucher_list.minimum.setValue(20000)
                self.choose(dialog.voucher_list.sort, "amount_desc")
                self.assertEqual(self.codes(dialog.voucher_list), ["M100", "M20"])
                self.assertIn("2/9", dialog.tabs.tabText(1))
                dialog._cell_clicked(0, 6)
                popup = dialog.voucher_popup
                self.choose(popup.voucher_list.kind, "percentage")
                self.choose(popup.voucher_list.sort, "percentage_desc")
                self.assertEqual(self.codes(popup.voucher_list), ["P30", "P15", "P9"])
                self.assertIn("partial", popup.message.text())
                popup.load_account(a)
                self.assertEqual(self.codes(popup.voucher_list), ["P30", "P15", "P9"])
                self.assertEqual(a.result, original)
                dialog.table.selectRow(1)
                self.assertEqual(self.codes(dialog.voucher_list), ["B50"])
                self.assertEqual(dialog.voucher_list.minimum.value(), 20000)
                popup.load_account(b)
                self.assertEqual(self.codes(popup.voucher_list), [])
                popup.voucher_list.reset()
                self.assertEqual(self.codes(popup.voucher_list), ["B50"])
            finally:
                dialog.shutdown()
                dialog.close()

    def test_popup_combo_can_be_selected_without_closing_voucher_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp)/"accounts.json")
            a = store.upsert("SPC_ST=synthetic-popup-filter", "A")
            a.result = {"vouchers": copy.deepcopy(ROWS)}
            dialog = ShopeeDialog(store)
            dialog.show()
            app.processEvents()
            try:
                dialog._cell_clicked(0, 6)
                popup = dialog.voucher_popup
                box = popup.voucher_list.kind
                app.processEvents()
                QTest.qWait(100)
                arrow = QPoint(box.width() - 10, box.height() // 2)
                QTest.mouseMove(box, arrow)
                QTest.mousePress(box, Qt.LeftButton, pos=arrow)
                QTest.qWait(50)
                QTest.mouseRelease(box, Qt.LeftButton, pos=arrow)
                app.processEvents()
                self.assertTrue(box.view().isVisible())
                target = box.view().visualRect(box.model().index(1, 0)).center()
                QTest.mouseMove(box.view().viewport(), target)
                QTest.mouseClick(box.view().viewport(), Qt.LeftButton, pos=target)
                app.processEvents()
                self.assertEqual(box.currentData(), "amount")
                self.assertTrue(popup.isVisible())
                self.assertEqual(self.codes(popup.voucher_list), ["M100", "M9", "M20", "M0"])
            finally:
                dialog.shutdown()
                dialog.close()
