"""Cookie jar parse / merge / Shopee dump."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from controlios.cookies import (
    Cookie,
    dump_from_folder,
    encode_binarycookies,
    export_dump,
    merge_cookies,
    parse_binarycookies,
    parse_cookies_reply,
    reconstruct_shopee_cookies,
)


SHOPEE_DUMP = Path("7gb8/com.beeasy.shopee.vn/20260917-111651")


class CookieCodecTest(unittest.TestCase):
    def test_binarycookies_round_trip(self) -> None:
        original = [
            Cookie(name="SPC_ST", value="st-1", domain=".shopee.vn", path="/",
                   expires=2_000_000_000, flags=1, source="jar"),
            Cookie(name="csrftoken", value="csrf-1", domain=".shopee.vn", path="/",
                   flags=1, source="jar"),
        ]
        parsed = parse_binarycookies(encode_binarycookies(original), "Cookies.binarycookies")
        self.assertEqual([c.name for c in parsed], ["SPC_ST", "csrftoken"])
        self.assertEqual(parsed[0].value, "st-1")
        self.assertEqual(parsed[0].domain, ".shopee.vn")
        self.assertTrue(parsed[0].secure)

    def test_merge_jar_wins_same_name(self) -> None:
        jar = [Cookie(name="SPC_EC", value="from-jar", domain=".shopee.vn")]
        reconstructed = [
            Cookie(name="SPC_EC", value="from-login", domain=".shopee.vn", reconstructed=True),
            Cookie(name="SPC_F", value="device", domain=".shopee.vn", reconstructed=True),
        ]
        merged = merge_cookies(jar, reconstructed)
        by_name = {c.name: c for c in merged}
        self.assertEqual(by_name["SPC_EC"].value, "from-jar")
        self.assertFalse(by_name["SPC_EC"].reconstructed)
        self.assertEqual(by_name["SPC_F"].value, "device")
        self.assertTrue(by_name["SPC_F"].reconstructed)

    def test_reconstruct_only_fills_gaps(self) -> None:
        session = {"accessToken": "tok", "userid": "16546135936", "deviceId": "dev"}
        reconstructed = reconstruct_shopee_cookies(session, ".shopee.vn")
        names = [c.name for c in reconstructed]
        self.assertIn("SPC_EC", names)
        self.assertIn("SPC_F", names)
        self.assertIn("SPC_U", names)
        self.assertNotIn("SPC_ST", names)
        self.assertNotIn("csrftoken", names)


class CookieDumpTest(unittest.TestCase):
    def test_parse_cookies_reply(self) -> None:
        payload = {
            "bundleId": "com.beeasy.shopee.vn",
            "staging": "/var/mobile/controlios-cookies/com.beeasy.shopee.vn",
            "files": ["data/Library/Cookies/Cookies.binarycookies"],
        }
        reply = parse_cookies_reply("OK\n" + json.dumps(payload) + "\n", "com.beeasy.shopee.vn")
        self.assertEqual(reply["staging"], payload["staging"])
        with self.assertRaises(ValueError):
            parse_cookies_reply("NOT_FOUND\n", "com.beeasy.shopee.vn")

    def test_export_dump_writes_three_files(self) -> None:
        dump = {
            "bundleId": "com.beeasy.shopee.vn",
            "header": "SPC_ST=st; csrftoken=csrf",
            "session": {"userid": "1"},
            "cookies": [
                {"name": "SPC_ST", "value": "st", "domain": ".shopee.vn",
                 "path": "/", "expires": 0, "secure": True, "source": "jar",
                 "reconstructed": False},
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            written = export_dump(dump, folder)
            names = {path.name for path in written}
            self.assertEqual(names, {"cookies.txt", "cookie-header.txt", "cookies.json"})
            header = Path(folder, "cookie-header.txt").read_text(encoding="utf-8")
            self.assertIn("SPC_ST=st", header)
            payload = json.loads(Path(folder, "cookies.json").read_text(encoding="utf-8"))
            self.assertNotIn("pw", json.dumps(payload))

    def test_staging_sqlite_uses_wal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_dir = root / "raw" / "data" / "Library" / "HTTPStorages"
            db_dir.mkdir(parents=True)
            db = db_dir / "httpstorages.sqlite"
            con = sqlite3.connect(db)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "CREATE TABLE cookies (name TEXT, value TEXT, domain TEXT, "
                "path TEXT, expires INTEGER, is_secure INTEGER)"
            )
            con.execute(
                "INSERT INTO cookies VALUES (?,?,?,?,?,?)",
                ("SPC_ST", "from-wal", ".shopee.vn", "/", 2_000_000_000, 1),
            )
            con.commit()
            con.close()
            dump = dump_from_folder(root, "com.beeasy.shopee.vn")
            names = {c["name"]: c["value"] for c in dump["cookies"]}
            self.assertEqual(names.get("SPC_ST"), "from-wal")
            self.assertTrue(dump["jarFound"])
            self.assertNotIn("SPC_ST", dump["missing"])

    @unittest.skipUnless(SHOPEE_DUMP.exists(), "local Shopee snapshot not present")
    def test_old_documents_snapshot_is_not_http_jar(self) -> None:
        dump = dump_from_folder(SHOPEE_DUMP, "com.beeasy.shopee.vn")
        self.assertFalse(dump["jarFound"])
        self.assertIn("SPC_ST", dump["missing"])
        self.assertIn("SPC_SI", dump["missing"])
        self.assertIn("csrftoken", dump["missing"])
        session = dump.get("session") or {}
        blob = json.dumps(session)
        self.assertNotIn('"pw"', blob)
        self.assertNotIn("password", blob.lower())
        names = {c["name"] for c in dump["cookies"]}
        self.assertTrue({"SPC_EC", "SPC_F", "SPC_U"} & names)


if __name__ == "__main__":
    unittest.main()
