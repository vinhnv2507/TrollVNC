"""Hạ yêu cầu iOS / đời máy trong file .ipa."""

from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ipa_patch import describe, find_info_plist, main, patch   # noqa: E402


def make_ipa(path: Path, info: dict, binary: bool = True,
             extra: dict | None = None) -> Path:
    fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/Demo.app/Info.plist", plistlib.dumps(info, fmt=fmt))
        archive.writestr("Payload/Demo.app/Demo", b"\xca\xfe\xba\xbe fake macho")
        archive.writestr("Payload/Demo.app/embedded.mobileprovision", b"provision")
        # App phụ cũng có Info.plist — không được sửa nhầm vào đây.
        archive.writestr("Payload/Demo.app/PlugIns/Ext.appex/Info.plist",
                         plistlib.dumps({"MinimumOSVersion": "16.0"}, fmt=fmt))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


class IpaPatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = make_ipa(self.dir / "demo.ipa", {
            "CFBundleIdentifier": "com.demo.app",
            "CFBundleDisplayName": "Demo",
            "CFBundleShortVersionString": "1.2.3",
            "MinimumOSVersion": "16.0",
            "UIRequiredDeviceCapabilities": ["arm64", "arm64e", "metal"],
        })
        self.out = self.dir / "out.ipa"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_describe_reads_the_requirements(self) -> None:
        info = describe(self.src)
        self.assertEqual(info["bundle_id"], "com.demo.app")
        self.assertEqual(info["min_ios"], "16.0")
        self.assertIn("arm64e", info["capabilities"])

    def test_lowering_min_ios(self) -> None:
        patch(self.src, self.out, min_ios="14.0")
        self.assertEqual(describe(self.out)["min_ios"], "14.0")

    def test_the_main_app_plist_is_the_one_patched(self) -> None:
        """App phụ cũng có Info.plist — sửa nhầm vào đó là vô ích."""

        with zipfile.ZipFile(self.src) as archive:
            self.assertEqual(find_info_plist(archive), "Payload/Demo.app/Info.plist")

        patch(self.src, self.out, min_ios="14.0")
        with zipfile.ZipFile(self.out) as archive:
            ext = plistlib.loads(archive.read("Payload/Demo.app/PlugIns/Ext.appex/Info.plist"))
        self.assertEqual(ext["MinimumOSVersion"], "16.0", "không được đụng app phụ")

    def test_dropping_device_gates_keeps_the_essential_ones(self) -> None:
        patch(self.src, self.out, drop_capabilities=True)
        caps = describe(self.out)["capabilities"]
        self.assertIn("arm64", caps, "arm64 là kiến trúc thật, không được bỏ")
        self.assertNotIn("arm64e", caps)
        self.assertNotIn("metal", caps)

    def test_everything_else_is_copied_byte_for_byte(self) -> None:
        """Chỉ được thay đúng một file, phần còn lại phải nguyên vẹn."""

        patch(self.src, self.out, min_ios="14.0")
        with zipfile.ZipFile(self.src) as a, zipfile.ZipFile(self.out) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for name in a.namelist():
                if name == "Payload/Demo.app/Info.plist":
                    continue
                self.assertEqual(a.read(name), b.read(name), name)

    def test_binary_plist_stays_binary(self) -> None:
        patch(self.src, self.out, min_ios="14.0")
        with zipfile.ZipFile(self.out) as archive:
            raw = archive.read("Payload/Demo.app/Info.plist")
        self.assertEqual(raw[:8], b"bplist00", "phải giữ nguyên định dạng nhị phân")

    def test_xml_plist_stays_xml(self) -> None:
        src = make_ipa(self.dir / "xml.ipa", {"MinimumOSVersion": "16.0"}, binary=False)
        patch(src, self.out, min_ios="14.0")
        with zipfile.ZipFile(self.out) as archive:
            raw = archive.read("Payload/Demo.app/Info.plist")
        self.assertTrue(raw.lstrip().startswith(b"<?xml"))

    def test_refuses_to_overwrite_the_source(self) -> None:
        with self.assertRaises(ValueError):
            patch(self.src, self.src, min_ios="14.0")

    def test_a_file_that_is_not_an_ipa_is_rejected(self) -> None:
        bad = self.dir / "bad.ipa"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("readme.txt", "khong phai ipa")
        with self.assertRaises(ValueError):
            describe(bad)

    def test_show_only_does_not_write_anything(self) -> None:
        before = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(main([str(self.src), "--show"]), 0)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), before)

    def test_cli_writes_a_patched_file_next_to_the_source(self) -> None:
        self.assertEqual(main([str(self.src), "--min-ios", "13.0"]), 0)
        produced = self.dir / "demo-patched.ipa"
        self.assertTrue(produced.exists())
        self.assertEqual(describe(produced)["min_ios"], "13.0")

    def test_missing_file_is_reported(self) -> None:
        self.assertEqual(main([str(self.dir / "khong-co.ipa"), "--show"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
