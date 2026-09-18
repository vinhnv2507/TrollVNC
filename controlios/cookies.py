"""Parse, merge and export iOS HTTP cookies (binarycookies, sqlite, Shopee login).

Python 3.13 in this project cannot import plistlib (missing pyexpat), so binary
plist / NSKeyedArchiver handling is implemented here instead of using plistlib.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote

MAC_EPOCH = 978307200  # 2001-01-01 UTC

SHOPEE_JAR_MARKERS = ("SPC_ST", "SPC_SI", "csrftoken")
LOGIN_REL_FILES = (
    "Documents/user/bt.userLoginInfo",
    "Documents/user/bt.userLastLoginInfo",
    "Documents/beeshop/kBTDeviceIDKey",
    "Documents/sz-uuid2",
)
COOKIE_REL_DIRS = (
    "Library/Cookies",
    "Library/HTTPStorages",
    "Library/WebKit/WebsiteData",
    "Documents/user",
    "Documents/beeshop",
)
SKIP_DIR_NAMES = {
    "Caches", "tmp", "IndexedDB", "CacheStorage", "ServiceWorkers",
    "WebKitCache", "fsCachedData", "OfflineWebApplicationCache",
    "LocalStorage", "WebSQL",
}


class UID:
    """Binary plist unique-object reference (NSKeyedArchiver)."""

    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = int(value)

    def __repr__(self) -> str:
        return f"UID({self.value})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UID) and other.value == self.value

    def __hash__(self) -> int:
        return hash(("UID", self.value))


@dataclass
class Cookie:
    name: str
    value: str
    domain: str = ""
    path: str = "/"
    expires: int = 0
    flags: int = 0
    source: str = ""
    reconstructed: bool = False

    @property
    def secure(self) -> bool:
        return bool(self.flags & 0x1)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["secure"] = self.secure
        return data


def cookie_domain_for_bundle(bundle_id: str) -> str:
    lower = (bundle_id or "").lower()
    if "shopee" in lower:
        if lower.endswith(".vn") or ".vn" in lower:
            return ".shopee.vn"
        return ".shopee.com"
    parts = [p for p in lower.split(".") if p]
    if len(parts) >= 2:
        return "." + ".".join(parts[-2:])
    return ""


def cookie_kind_for_name(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".binarycookies") or lower == "cookies.binarycookies":
        return "binarycookies"
    if lower.endswith(".sqlite") or lower.endswith(".sqlite3") or lower.endswith(".db"):
        if "cookie" in lower or lower in {"httpstorages.sqlite", "cookies.sqlite"}:
            return "sqlite"
        return "sqlite" if "httpstorages" in lower else "other"
    if lower in {"bt.userlogininfo", "bt.userlastlogininfo", "kbtdeviceidkey"}:
        return "login"
    if lower in {"sz-uuid2"}:
        return "uuid"
    return "other"


def looks_like_cookie_file(name: str) -> bool:
    kind = cookie_kind_for_name(name)
    return kind in {"binarycookies", "sqlite", "login", "uuid"}


# --------------------------------------------------------------------------- binarycookies

def _u32be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _u32le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _f64le(data: bytes, offset: int) -> float:
    return struct.unpack_from("<d", data, offset)[0]


def _cstr(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset)
    if end < 0:
        chunk = data[offset:]
    else:
        chunk = data[offset:end]
    if not chunk:
        return ""
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        return chunk.decode("latin-1", errors="replace")


def _mac_to_unix(value: float) -> int:
    if not value:
        return 0
    try:
        unix = int(value + MAC_EPOCH)
    except (OverflowError, ValueError):
        return 0
    return unix if unix > 0 else 0


def parse_binarycookies(data: bytes, source: str = "") -> list[Cookie]:
    """Parse Apple NSHTTPCookie binarycookies with bounds checks."""

    if len(data) < 8 or data[:4] != b"cook":
        return []
    num_pages = _u32be(data, 4)
    header_end = 8 + num_pages * 4
    if num_pages <= 0 or num_pages > 4096 or header_end > len(data):
        return []
    page_sizes = [_u32be(data, 8 + i * 4) for i in range(num_pages)]
    offset = header_end
    cookies: list[Cookie] = []
    for size in page_sizes:
        if size <= 0 or offset + size > len(data):
            break
        cookies.extend(_parse_cookie_page(data[offset:offset + size], source))
        offset += size
    return cookies


def _parse_cookie_page(page: bytes, source: str) -> list[Cookie]:
    if len(page) < 16 or _u32be(page, 0) != 0x00000100:
        return []
    count = _u32le(page, 4)
    if count <= 0 or count > 10000:
        return []
    table = 8
    if table + count * 4 + 4 > len(page):
        return []
    cookies: list[Cookie] = []
    for i in range(count):
        rec_off = _u32le(page, table + i * 4)
        parsed = _parse_cookie_record(page, rec_off, source)
        if parsed is not None:
            cookies.append(parsed)
    return cookies


def _parse_cookie_record(page: bytes, offset: int, source: str) -> Optional[Cookie]:
    if offset < 0 or offset + 56 > len(page):
        return None
    size = _u32le(page, offset)
    if size < 56 or offset + size > len(page):
        return None
    rec = page[offset:offset + size]
    flags = _u32le(rec, 8)
    url_off = _u32le(rec, 16)
    name_off = _u32le(rec, 20)
    path_off = _u32le(rec, 24)
    value_off = _u32le(rec, 28)
    expiry = _mac_to_unix(_f64le(rec, 40))
    name = _cstr(rec, name_off)
    if not name:
        return None
    return Cookie(
        name=name,
        value=_cstr(rec, value_off),
        domain=_cstr(rec, url_off),
        path=_cstr(rec, path_off) or "/",
        expires=expiry,
        flags=flags,
        source=source,
        reconstructed=False,
    )


def encode_binarycookies(cookies: Iterable[Cookie]) -> bytes:
    """Build a one-page binarycookies blob (for tests / round-trip)."""

    records: list[bytes] = []
    for cookie in cookies:
        domain = (cookie.domain or "").encode("utf-8") + b"\x00"
        name = cookie.name.encode("utf-8") + b"\x00"
        path = (cookie.path or "/").encode("utf-8") + b"\x00"
        value = (cookie.value or "").encode("utf-8") + b"\x00"
        header_len = 56
        url_off = header_len
        name_off = url_off + len(domain)
        path_off = name_off + len(name)
        value_off = path_off + len(path)
        payload = domain + name + path + value
        size = header_len + len(payload)
        expiry = float(cookie.expires - MAC_EPOCH) if cookie.expires else 0.0
        rec = struct.pack(
            "<iiiiiiii", size, 0, int(cookie.flags), 0,
            url_off, name_off, path_off, value_off,
        )
        rec += b"\x00" * 8
        rec += struct.pack("<dd", expiry, 0.0)
        rec += payload
        records.append(rec)

    count = len(records)
    first = 8 + 4 * count + 4
    offsets: list[int] = []
    body = b""
    cursor = first
    for rec in records:
        offsets.append(cursor)
        body += rec
        cursor += len(rec)
    page = struct.pack(">I", 0x00000100) + struct.pack("<I", count)
    page += b"".join(struct.pack("<I", off) for off in offsets)
    page += b"\x00\x00\x00\x00"
    page += body
    return b"cook" + struct.pack(">I", 1) + struct.pack(">I", len(page)) + page

# --------------------------------------------------------------------------- sqlite cookies

def parse_sqlite_cookies(data: bytes, source: str = "") -> list[Cookie]:
    """Parse Safari/CFNetwork/Chrome-style cookie sqlite blobs."""

    if not data or len(data) < 16:
        return []
    if data[:15] != b"SQLite format 3":
        return []
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        return parse_sqlite_cookies_path(tmp.name, source)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def parse_sqlite_cookies_path(path: str | Path, source: str = "") -> list[Cookie]:
    cookies: list[Cookie] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            con = sqlite3.connect(str(path))
        except sqlite3.Error:
            return []
    try:
        con.row_factory = sqlite3.Row
        tables = [
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        for table in tables:
            if table.startswith("sqlite_"):
                continue
            cookies.extend(_parse_sqlite_table(con, table, source))
    except sqlite3.Error:
        return cookies
    finally:
        con.close()
    return cookies


def _parse_sqlite_table(con: sqlite3.Connection, table: str, source: str) -> list[Cookie]:
    try:
        info = list(con.execute(f'PRAGMA table_info("{table}")'))
    except sqlite3.Error:
        return []
    cols = {str(row[1]).lower(): str(row[1]) for row in info}
    name_col = _first_col(cols, ("name", "cookie_name"))
    value_col = _first_col(cols, ("value", "cookie_value"))
    domain_col = _first_col(cols, ("domain", "host", "host_key", "origin", "origin_url"))
    path_col = _first_col(cols, ("path",))
    expires_col = _first_col(cols, (
        "expires", "expiry", "expires_utc", "expiration", "expiresdate",
        "expiry_date", "expirationdate",
    ))
    secure_col = _first_col(cols, ("is_secure", "issecure", "secure", "is_httponly"))
    if not name_col or not value_col:
        return []
    # Cookie tables usually have a domain/host column.
    if not domain_col and "cookie" not in table.lower():
        return []
    quoted = ", ".join(
        f'"{cols[c]}"' for c in (
            name_col, value_col, domain_col or name_col,
            path_col or name_col, expires_col or name_col, secure_col or name_col,
        )
    )
    try:
        rows = con.execute(f'SELECT {quoted} FROM "{table}"')
    except sqlite3.Error:
        return []
    cookies: list[Cookie] = []
    for row in rows:
        name = _cell_str(row[0])
        if not name:
            continue
        cookies.append(Cookie(
            name=name,
            value=_cell_str(row[1]),
            domain=_cell_str(row[2]) if domain_col else "",
            path=_cell_str(row[3]) if path_col else "/",
            expires=_normalize_expiry(row[4]) if expires_col else 0,
            flags=1 if (secure_col and _cell_truthy(row[5])) else 0,
            source=source,
            reconstructed=False,
        ))
    return cookies


def _first_col(cols: dict[str, str], names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        if name in cols:
            return name
    return None


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin-1", errors="replace")
    return str(value)


def _cell_truthy(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return value != 0
    text = _cell_str(value).strip().lower()
    return text in {"1", "true", "yes"}


def _normalize_expiry(value: Any) -> int:
    if value is None:
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    # Chrome expires_utc: microseconds since 1601-01-01.
    if number > 10_000_000_000_000:
        unix = int(number / 1_000_000 - 11_644_473_600)
        return unix if unix > 0 else 0
    if number > 10_000_000_000:  # milliseconds
        return int(number / 1000)
    if 1_000_000_000 < number < 4_000_000_000:
        return int(number)
    # Mac absolute time (seconds since 2001).
    if 0 < number < 1_000_000_000:
        return _mac_to_unix(number)
    return int(number)


# --------------------------------------------------------------------------- binary plist / NSKeyedArchiver

def _plistlib_mod():
    try:
        import plistlib
        return plistlib
    except Exception:
        return None


def loads_plist(data: bytes) -> Any:
    """Load XML or binary plist. Prefer stdlib; fall back to a local parser."""

    if not data:
        return None
    mod = _plistlib_mod()
    if mod is not None:
        try:
            return mod.loads(data)
        except Exception:
            pass
    if data.startswith(b"bplist00"):
        return parse_bplist(data)
    if data.lstrip().startswith(b"<?xml") or data.lstrip().startswith(b"<plist"):
        raise ValueError("xml plist requires plistlib")
    return None


def parse_bplist(data: bytes) -> Any:
    if len(data) < 40 or not data.startswith(b"bplist00"):
        raise ValueError("not a binary plist")
    trailer = data[-32:]
    offset_size = trailer[6]
    ref_size = trailer[7]
    num_objects = struct.unpack(">Q", trailer[8:16])[0]
    top_object = struct.unpack(">Q", trailer[16:24])[0]
    table_offset = struct.unpack(">Q", trailer[24:32])[0]
    if offset_size not in (1, 2, 4, 8) or ref_size not in (1, 2, 4, 8):
        raise ValueError("bad bplist sizes")
    if num_objects <= 0 or num_objects > 2_000_000:
        raise ValueError("bad bplist object count")
    table_end = table_offset + num_objects * offset_size
    if table_offset < 8 or table_end > len(data) - 32:
        raise ValueError("bad bplist offset table")

    def read_int(buf: bytes, size: int) -> int:
        if size == 1:
            return buf[0]
        if size == 2:
            return struct.unpack(">H", buf)[0]
        if size == 4:
            return struct.unpack(">I", buf)[0]
        return struct.unpack(">Q", buf)[0]

    offsets = [
        read_int(data[table_offset + i * offset_size:table_offset + (i + 1) * offset_size], offset_size)
        for i in range(num_objects)
    ]
    parsed: dict[int, Any] = {}
    visiting: set[int] = set()

    def object_at(index: int) -> Any:
        if index in parsed:
            return parsed[index]
        if index in visiting or index < 0 or index >= num_objects:
            return None
        visiting.add(index)
        value = parse_object(offsets[index])
        visiting.remove(index)
        parsed[index] = value
        return value

    def parse_size(offset: int, marker_len: int) -> tuple[int, int]:
        length = marker_len
        pos = offset + 1
        if length == 0x0F:
            size_marker = data[pos]
            pos += 1
            if (size_marker & 0xF0) != 0x10:
                raise ValueError("bad bplist size")
            nbytes = 1 << (size_marker & 0x0F)
            length = read_int(data[pos:pos + nbytes], nbytes)
            pos += nbytes
        return length, pos

    def parse_refs(pos: int, count: int) -> list[Any]:
        out = []
        for i in range(count):
            start = pos + i * ref_size
            idx = read_int(data[start:start + ref_size], ref_size)
            out.append(object_at(idx))
        return out

    def parse_object(offset: int) -> Any:
        if offset < 0 or offset >= len(data):
            return None
        marker = data[offset]
        kind = marker & 0xF0
        extra = marker & 0x0F
        if marker == 0x00:
            return None
        if marker == 0x08:
            return False
        if marker == 0x09:
            return True
        if kind == 0x10:
            nbytes = 1 << extra
            raw = data[offset + 1:offset + 1 + nbytes]
            if nbytes == 1:
                return raw[0]
            if nbytes == 2:
                return struct.unpack(">H", raw)[0]
            if nbytes == 4:
                return struct.unpack(">I", raw)[0]
            if nbytes == 8:
                return struct.unpack(">q", raw)[0]
            return int.from_bytes(raw, "big", signed=True)
        if marker == 0x22:
            return struct.unpack(">f", data[offset + 1:offset + 5])[0]
        if marker == 0x23:
            return struct.unpack(">d", data[offset + 1:offset + 9])[0]
        if marker == 0x33:
            return struct.unpack(">d", data[offset + 1:offset + 9])[0]
        if kind == 0x40:
            length, pos = parse_size(offset, extra)
            return bytes(data[pos:pos + length])
        if kind == 0x50:
            length, pos = parse_size(offset, extra)
            return data[pos:pos + length].decode("ascii", errors="replace")
        if kind == 0x60:
            length, pos = parse_size(offset, extra)
            return data[pos:pos + length * 2].decode("utf-16-be", errors="replace")
        if kind == 0x80:
            nbytes = extra + 1
            return UID(int.from_bytes(data[offset + 1:offset + 1 + nbytes], "big"))
        if kind in (0xA0, 0xC0):
            length, pos = parse_size(offset, extra)
            return parse_refs(pos, length)
        if kind == 0xD0:
            length, pos = parse_size(offset, extra)
            keys = parse_refs(pos, length)
            vals = parse_refs(pos + length * ref_size, length)
            return {keys[i]: vals[i] for i in range(length)}
        return None

    return object_at(top_object)


def uid_index(value: Any) -> Optional[int]:
    if isinstance(value, UID):
        return value.value
    data = getattr(value, "data", None)
    if isinstance(data, int) and type(value).__name__ == "UID":
        return data
    if isinstance(value, dict) and "CF$UID" in value:
        try:
            return int(value["CF$UID"])
        except (TypeError, ValueError):
            return None
    return None


def nskeyed_plain(data: bytes, skip_keys: Iterable[str] = ("pw", "password", "passwd")) -> Any:
    """Decode NSKeyedArchiver / raw plist into plain Python objects.

    Password fields are dropped so dumps never export ``pw``.
    """

    root = loads_plist(data)
    if root is None:
        text = _maybe_text(data)
        return text if text is not None else None
    if not isinstance(root, dict) or "$objects" not in root:
        return _plain_value(root, skip_keys=set(k.lower() for k in skip_keys))
    objects = list(root.get("$objects") or [])
    skip = {k.lower() for k in skip_keys}
    memo: dict[int, Any] = {}
    visiting: set[int] = set()

    def resolve_index(index: int) -> Any:
        if index in memo:
            return memo[index]
        if index in visiting or index < 0 or index >= len(objects):
            return None
        visiting.add(index)
        value = _resolve_object(objects[index], resolve_index, skip, visiting)
        visiting.remove(index)
        memo[index] = value
        return value

    top = root.get("$top") or {}
    if isinstance(top, dict) and "root" in top:
        idx = uid_index(top["root"])
        if idx is not None:
            return resolve_index(idx)
    idx = uid_index(top) if not isinstance(top, dict) else None
    if idx is not None:
        return resolve_index(idx)
    return {key: _resolve_object(val, resolve_index, skip, visiting) for key, val in (top.items() if isinstance(top, dict) else [])}


def _resolve_object(obj: Any, resolve_index, skip: set[str], visiting: set[int]) -> Any:
    idx = uid_index(obj)
    if idx is not None:
        return resolve_index(idx)
    if isinstance(obj, dict):
        if "NS.string" in obj:
            return _resolve_object(obj["NS.string"], resolve_index, skip, visiting)
        if "NS.keys" in obj and "NS.objects" in obj:
            keys = _resolve_object(obj["NS.keys"], resolve_index, skip, visiting) or []
            vals = _resolve_object(obj["NS.objects"], resolve_index, skip, visiting) or []
            out = {}
            for key, val in zip(keys, vals):
                if str(key).lower() in skip:
                    continue
                out[str(key)] = val
            return out
        if "NS.objects" in obj and "NS.keys" not in obj:
            return _resolve_object(obj["NS.objects"], resolve_index, skip, visiting)
        if set(obj.keys()) <= {"NS.time", "$class"} and "NS.time" in obj:
            return _resolve_object(obj["NS.time"], resolve_index, skip, visiting)
        out = {}
        for key, val in obj.items():
            if str(key).startswith("$"):
                continue
            if str(key).lower() in skip:
                continue
            out[str(key)] = _resolve_object(val, resolve_index, skip, visiting)
        return out
    if isinstance(obj, list):
        return [_resolve_object(item, resolve_index, skip, visiting) for item in obj]
    if obj == "$null":
        return None
    return obj


def _plain_value(obj: Any, skip_keys: set[str]) -> Any:
    if isinstance(obj, dict):
        return {
            str(k): _plain_value(v, skip_keys)
            for k, v in obj.items()
            if str(k).lower() not in skip_keys
        }
    if isinstance(obj, list):
        return [_plain_value(v, skip_keys) for v in obj]
    return obj


def _maybe_text(data: bytes) -> Optional[str]:
    if not data or b"\x00" in data[:64]:
        return None
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text or any(ord(ch) < 9 for ch in text):
        return None
    return text


def extract_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _maybe_text(value) or ""
    if isinstance(value, dict):
        for key in ("NS.string", "string", "value"):
            if key in value:
                return extract_string(value[key])
        return ""
    text = str(value).strip()
    return "" if text in {"None", "$null"} else text


# --------------------------------------------------------------------------- Shopee reconstruct / dump

def encode_spc_ec(token: str) -> str:
    return quote(token or "", safe="")


def shopee_session_from_plain(plain: Any, device_id: str = "", sz_uuid: str = "") -> dict[str, str]:
    session: dict[str, str] = {}
    data = plain if isinstance(plain, dict) else {}
    mapping = {
        "username": ("username",),
        "userid": ("userid", "userId", "user_id"),
        "phone": ("phone",),
        "accessToken": ("accessToken", "vcodeToken"),
        "email": ("email",),
    }
    for dest, keys in mapping.items():
        for key in keys:
            text = extract_string(data.get(key))
            if text:
                session[dest] = text
                break
    if device_id:
        session["deviceId"] = device_id
    if sz_uuid:
        session["szUuid"] = sz_uuid
    session.pop("pw", None)
    session.pop("password", None)
    return session


def reconstruct_shopee_cookies(session: dict[str, str], domain: str) -> list[Cookie]:
    cookies: list[Cookie] = []
    token = session.get("accessToken") or ""
    device = session.get("deviceId") or ""
    userid = session.get("userid") or ""
    if token:
        cookies.append(Cookie(
            name="SPC_EC", value=encode_spc_ec(token), domain=domain,
            path="/", flags=1, source="bt.userLoginInfo", reconstructed=True,
        ))
    if device:
        cookies.append(Cookie(
            name="SPC_F", value=device, domain=domain,
            path="/", flags=1, source="kBTDeviceIDKey", reconstructed=True,
        ))
    if userid:
        cookies.append(Cookie(
            name="SPC_U", value=userid, domain=domain,
            path="/", flags=1, source="bt.userLoginInfo", reconstructed=True,
        ))
        cookies.append(Cookie(
            name="SPC_U_ID", value=userid, domain=domain,
            path="/", flags=1, source="bt.userLoginInfo", reconstructed=True,
        ))
    return cookies



def merge_cookies(jar: Iterable[Cookie], reconstructed: Iterable[Cookie]) -> list[Cookie]:
    """Real HTTP jar cookies win; reconstructed tokens only fill missing names."""

    by_name: dict[str, Cookie] = {}
    order: list[str] = []
    for cookie in [c for c in jar if c.name]:
        if cookie.name not in by_name:
            order.append(cookie.name)
        by_name[cookie.name] = cookie
    for cookie in [c for c in reconstructed if c.name]:
        if cookie.name in by_name:
            continue
        order.append(cookie.name)
        by_name[cookie.name] = cookie
    return [by_name[name] for name in order]

def cookie_header(cookies: Iterable[Cookie]) -> str:
    return "; ".join(f"{c.name}={c.value}" for c in cookies if c.name)


def to_netscape(cookies: Iterable[Cookie]) -> str:
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by ControlIOS. Reconstructed cookies are marked in cookies.json.",
    ]
    for cookie in cookies:
        domain = cookie.domain or ""
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if cookie.secure else "FALSE"
        lines.append(
            f"{domain}\t{flag}\t{cookie.path or '/'}\t{secure}\t"
            f"{int(cookie.expires or 0)}\t{cookie.name}\t{cookie.value}"
        )
    return "\n".join(lines) + "\n"


def cookies_to_dicts(cookies: Iterable[Cookie]) -> list[dict[str, Any]]:
    return [c.as_dict() for c in cookies]


def parse_cookie_bytes(data: bytes, name: str, source: str = "") -> list[Cookie]:
    kind = cookie_kind_for_name(name)
    if kind == "binarycookies" or (data[:4] == b"cook"):
        return parse_binarycookies(data, source or name)
    if kind == "sqlite" or data[:15] == b"SQLite format 3":
        return parse_sqlite_cookies(data, source or name)
    return []


def _is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def iter_cookie_files(root: Path) -> Iterable[Path]:
    root = Path(root)
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os_walk_skip(root):
        for name in filenames:
            path = Path(dirpath) / name
            rel = name.lower()
            if looks_like_cookie_file(name) or rel.endswith(".binarycookies"):
                yield path


def os_walk_skip(root: Path):
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_skipped_dir(d)]
        yield dirpath, dirnames, filenames


def infer_bundle_id(path: Path, explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    for part in [path, *path.parents]:
        name = part.name
        if name.count(".") >= 2 and " " not in name and not name.startswith("."):
            if any(name.startswith(p) for p in ("com.", "vn.", "id.", "net.", "org.")):
                return name
            if "shopee" in name.lower():
                return name
    return ""


def find_container_root(path: Path) -> Path:
    path = Path(path)
    if path.is_file():
        path = path.parent
    candidates = [path]
    if path.exists():
        candidates.extend(sorted(p for p in path.iterdir() if p.is_dir())[:20])
    for base in candidates:
        for rel in LOGIN_REL_FILES:
            if (base / rel).exists():
                return base
        if (base / "Library" / "Cookies").exists() or (base / "Documents").exists():
            return base
    for rel_name in ("bt.userLoginInfo", "Cookies.binarycookies"):
        matches = list(path.rglob(rel_name))[:1]
        if matches:
            found = matches[0]
            if found.name == "bt.userLoginInfo":
                return found.parents[2] if len(found.parents) >= 2 else found.parent
            return found.parents[1] if found.parents else found.parent
    return path



def build_dump(bundle_id: str, files: dict[str, bytes],
               data_container: str = "", group_containers: Optional[list] = None,
               extra_jar: Optional[Iterable[Cookie]] = None,
               extra_sources: Optional[Iterable[str]] = None) -> dict[str, Any]:
    domain = cookie_domain_for_bundle(bundle_id)
    jar: list[Cookie] = list(extra_jar or [])
    sources: list[str] = [str(item) for item in (extra_sources or [])]
    login_plain: Any = None
    device_id = ""
    sz_uuid = ""
    for rel, data in files.items():
        if not data:
            continue
        name = Path(rel.replace("\\", "/")).name
        lower = rel.replace("\\", "/").lower()
        norm = rel.replace("\\", "/")
        if norm not in sources:
            sources.append(norm)
        if name.lower() in {"bt.userlogininfo", "bt.userlastlogininfo"}:
            plain = nskeyed_plain(data)
            if isinstance(plain, dict) and (login_plain is None or name.lower() == "bt.userlogininfo"):
                if name.lower() == "bt.userlogininfo" or login_plain is None:
                    login_plain = plain
            continue
        if name.lower() == "kbtdeviceidkey":
            device_id = extract_string(nskeyed_plain(data)) or device_id
            continue
        if name.lower() == "sz-uuid2":
            sz_uuid = _maybe_text(data) or extract_string(nskeyed_plain(data)) or sz_uuid
            continue
        parsed = parse_cookie_bytes(data, name, norm)
        if parsed:
            jar.extend(parsed)
        elif "cookie" in lower:
            pass

    session = shopee_session_from_plain(login_plain or {}, device_id, sz_uuid)
    reconstructed: list[Cookie] = []
    if "shopee" in (bundle_id or "").lower() or session.get("accessToken"):
        reconstructed = reconstruct_shopee_cookies(session, domain or ".shopee.vn")
    merged = merge_cookies(jar, reconstructed)
    names = {c.name for c in merged}
    jar_names = {c.name for c in jar}
    missing = [marker for marker in SHOPEE_JAR_MARKERS if marker not in names] if (
        "shopee" in (bundle_id or "").lower()
    ) else []
    return {
        "bundleId": bundle_id,
        "domain": domain,
        "dataContainer": data_container,
        "groupContainers": list(group_containers or []),
        "cookies": cookies_to_dicts(merged),
        "header": cookie_header(merged),
        "session": session,
        "sources": sources,
        "jarFound": bool(jar_names),
        "missing": missing,
    }


def _staging_root(path: Path) -> Optional[Path]:
    path = Path(path)
    raw = path / "raw"
    if (raw / "data").exists() or (raw / "group").exists():
        return raw
    if (path / "data").exists() or (path / "group").exists():
        return path
    return None


def _container_roots(path: Path) -> list[tuple[str, Path]]:
    staging = _staging_root(path)
    if staging is not None:
        roots: list[tuple[str, Path]] = []
        data = staging / "data"
        if data.exists():
            roots.append(("data", data))
        group = staging / "group"
        if group.is_dir():
            for child in sorted(group.iterdir()):
                if child.is_dir():
                    roots.append((f"group/{child.name}", child))
        return roots
    return [("", find_container_root(path))]


def _is_under_named_dir(path: Path, names: tuple[str, ...]) -> bool:
    lowered = {name.lower() for name in names}
    return any(part.lower() in lowered for part in path.parts)


def _sqlite_cookie_path(path: Path) -> bool:
    name = path.name.lower()
    if not (name.endswith(".sqlite") or name.endswith(".sqlite3") or name.endswith(".db")):
        return False
    if "cookie" in name or "httpstorages" in name:
        return True
    return _is_under_named_dir(path, ("Cookies", "HTTPStorages"))


def _should_take_file(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    if looks_like_cookie_file(name) or lower.endswith(".binarycookies"):
        return True
    if _is_under_named_dir(path, ("Cookies", "HTTPStorages")):
        return True
    return False


def dump_from_folder(path: str | Path, bundle_id: Optional[str] = None) -> dict[str, Any]:
    path = Path(path)
    roots = _container_roots(path)
    files: dict[str, bytes] = {}
    extra_jar: list[Cookie] = []
    extra_sources: list[str] = []
    data_container = ""
    group_containers: list[dict[str, str]] = []
    seen: set[str] = set()

    for prefix, root in roots:
        if prefix == "data":
            data_container = str(root)
        elif prefix.startswith("group/"):
            group_containers.append({"id": prefix.split("/", 1)[1], "path": str(root)})
        elif not prefix:
            data_container = str(root)

        def rel_of(fp: Path, prefix: str = prefix, root: Path = root) -> str:
            try:
                rel = fp.relative_to(root).as_posix()
            except ValueError:
                rel = fp.name
            return f"{prefix}/{rel}" if prefix else rel

        def consider(fp: Path) -> None:
            if not fp.is_file():
                return
            try:
                key = str(fp.resolve())
            except OSError:
                key = str(fp)
            if key in seen:
                return
            seen.add(key)
            rel = rel_of(fp)
            lower = fp.name.lower()
            if _sqlite_cookie_path(fp):
                extra_sources.append(rel)
                extra_jar.extend(parse_sqlite_cookies_path(fp, rel))
                return
            if lower.endswith("-wal") or lower.endswith("-shm"):
                extra_sources.append(rel)
                return
            if rel in files:
                return
            if not _should_take_file(fp):
                return
            try:
                files[rel] = fp.read_bytes()
            except OSError:
                return

        for rel in LOGIN_REL_FILES:
            candidate = root / rel
            if candidate.is_file():
                consider(candidate)

        scan_dirs = [root / rel for rel in COOKIE_REL_DIRS]
        for base in (root / "Library", root):
            if base.is_dir():
                for child in base.iterdir():
                    if child.is_dir() and child.name in {"Cookies", "HTTPStorages"}:
                        scan_dirs.append(child)
        visited: set[str] = set()
        for folder in scan_dirs:
            if not folder.exists():
                continue
            try:
                mark = str(folder.resolve())
            except OSError:
                mark = str(folder)
            if mark in visited:
                continue
            visited.add(mark)
            if folder.is_file():
                consider(folder)
                continue
            for dirpath, dirnames, filenames in os_walk_skip(folder):
                for name in filenames:
                    consider(Path(dirpath) / name)

    bundle = infer_bundle_id(path, bundle_id)
    if not bundle:
        for _prefix, root in roots:
            bundle = infer_bundle_id(root, bundle_id)
            if bundle:
                break
    return build_dump(
        bundle, files,
        data_container=data_container,
        group_containers=group_containers,
        extra_jar=extra_jar,
        extra_sources=extra_sources,
    )

def parse_cookies_reply(text: str, bundle_id: str = "") -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("NOT_FOUND"):
        raise ValueError(f"NOT_FOUND {bundle_id}".strip())
    if raw.startswith("OK"):
        raw = raw[2:].lstrip("\r\n")
    if not raw:
        raise ValueError("empty cookies reply")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("cookies reply is not an object")
    return data


def export_dump(dump: dict[str, Any], dest: str | Path) -> list[Path]:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    cookies = [
        Cookie(
            name=str(item.get("name") or ""),
            value=str(item.get("value") or ""),
            domain=str(item.get("domain") or ""),
            path=str(item.get("path") or "/"),
            expires=int(item.get("expires") or 0),
            flags=1 if item.get("secure") else 0,
            source=str(item.get("source") or ""),
            reconstructed=bool(item.get("reconstructed")),
        )
        for item in dump.get("cookies") or []
        if item.get("name")
    ]
    written = []
    txt = dest / "cookies.txt"
    txt.write_text(to_netscape(cookies), encoding="utf-8")
    written.append(txt)
    header = dest / "cookie-header.txt"
    header.write_text(dump.get("header") or cookie_header(cookies), encoding="utf-8")
    written.append(header)
    payload = dict(dump)
    payload["cookies"] = cookies_to_dicts(cookies)
    js = dest / "cookies.json"
    js.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    written.append(js)
    return written
