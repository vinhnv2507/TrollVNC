# -*- coding: utf-8 -*-
"""IOS file picker: 3uTools Documents that do not exist yet must still open."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from controlios.ui.app import IOSFileBrowserDialog  # noqa: E402

app = QApplication.instance() or QApplication([])

CONTAINER = "/var/mobile/Containers/Data/Application/UUID-3u"
DOCS = CONTAINER + "/Documents"


class _FakeFilePool:
    def __init__(self) -> None:
        self.list_calls: list[str] = []
        self.container_calls: list[str] = []
        self.pending: list[tuple] = []
        self.queue = False
        self.errors: dict[str, str] = {}
        self.entries: dict[str, list] = {}
        self.containers = {"notes.3u": CONTAINER}
        self.container_errors: dict[str, str] = {}
        self.downloads_path = (
            "/var/mobile/Containers/Shared/AppGroup/UUID-FP/"
            "File Provider Storage/Downloads"
        )
        self.downloads_error = None
        self.downloads_calls = 0

    def list_dir(self, key, path, on_done) -> None:
        self.list_calls.append(path)
        if self.queue:
            self.pending.append((key, path, on_done))
            return
        on_done(key, path, list(self.entries.get(path, [])), self.errors.get(path))

    def app_container(self, key, bundle_id, on_done) -> None:
        self.container_calls.append(bundle_id)
        err = self.container_errors.get(bundle_id)
        data = "" if err else self.containers.get(bundle_id, "")
        on_done(key, data, bundle_id, err)

    def find_files_downloads(self, key, on_done) -> None:
        self.downloads_calls += 1
        err = self.downloads_error
        data = "" if err else self.downloads_path
        on_done(key, data, err)


class FileBrowserDialogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = _FakeFilePool()
        self.dialog = None

    def tearDown(self) -> None:
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

    def _open(self) -> IOSFileBrowserDialog:
        self.dialog = IOSFileBrowserDialog(self.pool, "10.0.0.1:5901")
        return self.dialog

    def _select_3utools(self, dialog: IOSFileBrowserDialog) -> None:
        combo = dialog.preset_combo
        for index in range(combo.count()):
            if combo.itemData(index) == "@app:notes.3u":
                combo.setCurrentIndex(index)
                return
        self.fail("missing 3uTools preset")

    def test_missing_3utools_documents_opens_as_empty_folder(self) -> None:
        self.pool.errors["/var/mobile"] = "ERR CannotRead"
        self.pool.errors[DOCS] = "ERR CannotRead"
        dialog = self._open()
        self.assertEqual(dialog.path_edit.text(), "/var/mobile")
        self._select_3utools(dialog)
        self.assertEqual(self.pool.container_calls, ["notes.3u"])
        self.assertEqual(dialog.path, DOCS)
        self.assertEqual(dialog.path_edit.text(), DOCS)
        self.assertNotEqual(dialog.path_edit.text(), "/var/mobile")
        self.assertIn("0 m\u1ee5c", dialog.status.text())
        self.assertNotIn("CannotRead", dialog.status.text())
        self.assertEqual(dialog.table.rowCount(), 0)

    def test_missing_3utools_app_explains(self) -> None:
        self.pool.errors["/var/mobile"] = "ERR CannotRead"
        self.pool.container_errors["notes.3u"] = "ERR NotFound"
        dialog = self._open()
        self._select_3utools(dialog)
        self.assertIn("notes.3u", dialog.status.text())
        self.assertIn("3uTools", dialog.status.text())
        self.assertEqual(dialog.path_edit.text(), "/var/mobile")

    def test_stale_var_mobile_listing_does_not_overwrite_3utools_path(self) -> None:
        self.pool.queue = True
        dialog = self._open()
        self.assertEqual(len(self.pool.pending), 1)
        stale_key, stale_path, stale_cb = self.pool.pending.pop(0)
        self.assertEqual(stale_path, "/var/mobile")
        self._select_3utools(dialog)
        docs_calls = [item for item in self.pool.pending if item[1] == DOCS]
        self.assertEqual(len(docs_calls), 1)
        docs_key, docs_path, docs_cb = docs_calls[0]
        docs_cb(docs_key, docs_path, [], "ERR CannotRead")
        self.assertEqual(dialog.path_edit.text(), DOCS)
        stale_cb(stale_key, stale_path, [], "ERR CannotRead")
        self.assertEqual(dialog.path_edit.text(), DOCS)
        self.assertIn("0 m\u1ee5c", dialog.status.text())
        self.assertNotIn("CannotRead", dialog.status.text())
        self.assertNotEqual(dialog.path_edit.text(), "/var/mobile")

    def _select_data(self, dialog, data: str) -> None:
        combo = dialog.preset_combo
        for index in range(combo.count()):
            if combo.itemData(index) == data:
                combo.setCurrentIndex(index)
                return
        self.fail("missing preset " + data)

    def test_files_downloads_preset_opens_file_provider_folder(self) -> None:
        dialog = self._open()
        self._select_data(dialog, "@files:downloads")
        self.assertEqual(self.pool.downloads_calls, 1)
        self.assertTrue(dialog.selected_files_downloads)
        self.assertEqual(dialog.path, self.pool.downloads_path)
        self.assertEqual(dialog.downloads_relpath(), "")
        self.assertEqual(dialog.preset_combo.currentData(), "@files:downloads")

    def test_missing_files_downloads_folder_still_opens(self) -> None:
        self.pool.errors[self.pool.downloads_path] = "ERR CannotRead"
        dialog = self._open()
        self._select_data(dialog, "@files:downloads")
        self.assertEqual(dialog.path, self.pool.downloads_path)
        self.assertNotIn("CannotRead", dialog.status.text())
        self.assertTrue(dialog.selected_files_downloads)

    def test_both_3utools_and_downloads_marks_two_destinations(self) -> None:
        dialog = self._open()
        self._select_data(dialog, "@both:3u+downloads")
        self.assertEqual(dialog.selected_app_bundle, "notes.3u")
        self.assertTrue(dialog.selected_files_downloads)
        self.assertTrue(dialog.select_button.isEnabled())

    def test_files_downloads_error_explains(self) -> None:
        self.pool.downloads_error = "ERR CannotRead"
        dialog = self._open()
        self._select_data(dialog, "@files:downloads")
        self.assertFalse(dialog.selected_files_downloads)
        self.assertIn("Tải về", dialog.status.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)