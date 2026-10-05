"""Local Shopee account library and read-only checks through per-account proxies."""
from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit, urlunsplit

import requests


class ShopeeError(ValueError):
    """An error safe to display without exposing credentials."""


def normalize_cookie(value: str) -> str:
    value = re.sub(r"^Cookie\s*:\s*", "", value.strip(), flags=re.I)
    if "\r" in value or "\n" in value:
        raise ShopeeError("Cookie phải nằm trên một dòng.")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ShopeeError("Cookie chứa ký tự điều khiển không hợp lệ.")
    if "=" not in value and value:
        value = "SPC_ST=" + value
    parts = [p.strip() for p in value.split(";") if p.strip()]
    if not parts or any("=" not in p or not p.split("=", 1)[0].strip() for p in parts):
        raise ShopeeError("Cookie không hợp lệ; dùng SPC_ST=… hoặc cookie đầy đủ.")
    header = "; ".join(parts)
    if not cookie_value(header, "SPC_ST"):
        raise ShopeeError("Cookie thiếu SPC_ST.")
    return header


def cookie_value(header: str, name: str) -> str:
    for part in header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value
    return ""


def normalize_proxy(value: str) -> str:
    value = value.strip()
    if not value:
        raise ShopeeError("Chưa gán proxy; hãy gán proxy trước khi kiểm tra.")
    if any(c.isspace() for c in value):
        raise ShopeeError("Proxy không hợp lệ: không dùng khoảng trắng.")
    if "://" not in value:
        parts = value.split(":", 3)
        if len(parts) == 4:
            host, port, user, password = parts
            value = f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        else:
            value = "http://" + value
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme.lower() not in {"http", "https", "socks5", "socks5h"}
                or not parsed.hostname or port is None or not 1 <= port <= 65535
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError
    except ValueError:
        raise ShopeeError("Proxy không hợp lệ: host:port:user:pass hoặc URL HTTP/HTTPS/SOCKS5.") from None
    # socks5h resolves destination names through the proxy as well.
    scheme = "socks5h" if parsed.scheme in {"socks5", "socks5h"} else parsed.scheme
    return urlunsplit((scheme, parsed.netloc, "", "", ""))


def proxy_label(value: str) -> str:
    if not value:
        return "Chưa gán"
    try:
        parsed = urlsplit(normalize_proxy(value))
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    except ShopeeError:
        return "Proxy chưa hợp lệ"


def proxy_assignment(lines: list[str], count: int, mode: str) -> list[str]:
    """Validate the entire list before preparing assignments in table order."""
    if count <= 0:
        raise ShopeeError("Không có tài khoản để gán proxy.")
    if mode not in {"sequential", "cycle", "shared"}:
        raise ShopeeError("Cách gán proxy không hợp lệ.")
    proxies = [normalize_proxy(line) for line in lines if line.strip()]
    if not proxies:
        raise ShopeeError("Hãy nhập ít nhất một proxy.")
    if mode == "shared":
        if len(proxies) != 1:
            raise ShopeeError("Dùng chung yêu cầu đúng một proxy.")
        return proxies * count
    if mode == "cycle":
        return [proxies[index % len(proxies)] for index in range(count)]
    return proxies[:count] + [""] * max(0, count - len(proxies))


@dataclass
class Account:
    label: str
    cookie: str = field(repr=False)
    proxy: str = field(default="", repr=False)
    source: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    checked_at: str = ""
    result: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        return hashlib.sha256((self.cookie + "\0" + self.proxy).encode()).hexdigest()


class AccountStore:
    def __init__(self, path: Path):
        self.path = path
        self.accounts: list[Account] = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
                    raise ValueError
                allowed = Account.__dataclass_fields__
                self.accounts = [Account(**{k: v for k, v in row.items() if k in allowed})
                                 for row in data["accounts"]]
                if any(not all(isinstance(getattr(a, key), str) for key in
                               ("label", "cookie", "proxy", "source", "id", "checked_at"))
                       or not isinstance(a.result, dict) for a in self.accounts):
                    raise ValueError
                if len({a.id for a in self.accounts}) != len(self.accounts):
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ShopeeError("Không đọc được danh sách Shopee đã lưu; file gốc được giữ nguyên.") from None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             delete=False, suffix=".tmp") as stream:
                tmp_path = Path(stream.name)
                json.dump({"version": 1, "accounts": [asdict(a) for a in self.accounts]},
                          stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            tmp_path.replace(self.path)
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()

    def upsert(self, cookie: str, label: str = "", proxy: str = "", source: str = "") -> Account:
        cookie = normalize_cookie(cookie)
        if proxy:
            proxy = normalize_proxy(proxy)
        token = cookie_value(cookie, "SPC_ST")
        # One captured device updates its account when the login session changes.
        account = next((a for a in self.accounts if source and a.source == source), None)
        if account is None:
            account = next((a for a in self.accounts if cookie_value(a.cookie, "SPC_ST") == token), None)
        if account is None:
            account = Account(label=label or f"Shopee {len(self.accounts) + 1}", cookie=cookie,
                              proxy=proxy, source=source)
            self.accounts.append(account)
        else:
            changed = account.cookie != cookie or (proxy and account.proxy != proxy)
            account.cookie = cookie
            if proxy:
                account.proxy = proxy
            if source:
                account.source = source
            if changed:
                account.result = {}
                account.checked_at = ""
        return account


def _text(value) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    text = str(value)
    if not text.isdigit() and re.fullmatch(r"(?:[0-9a-fA-F]{2}){4,}", text):
        try:
            decoded = bytes.fromhex(text).decode("utf-8")
            if decoded.isprintable():
                text = decoded
        except (ValueError, UnicodeError):
            pass
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", text)).split())


def _find(data, keys: tuple[str, ...]):
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return value
        for value in data.values():
            found = _find(value, keys)
            if found != "":
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find(value, keys)
            if found != "":
                return found
    return ""


def _objects(data):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _objects(value)
    elif isinstance(data, list):
        for value in data:
            yield from _objects(value)


def _money(value) -> str:
    try:
        # API amounts are VND multiplied by 100000, including small amounts.
        return f"{float(value) / 100000:,.0f}".replace(",", ".") + "đ"
    except (ValueError, TypeError):
        return ""


def parse_voucher(item: dict) -> dict:
    info = item.get("voucher") or item.get("voucher_info") or item
    code = _text(_find(info, ("voucher_code", "code", "voucherCode")))
    percentage = _find(info, ("discount_percentage", "reward_percentage"))
    value = _find(info, ("discount_value", "reward_value"))
    try:
        discount = f"{percentage}%" if percentage and float(percentage) > 0 else _money(value)
    except (ValueError, TypeError):
        discount = _money(value)
    if not discount and info.get("reward_type") == 2:
        discount = "Miễn phí vận chuyển"
    expires = ""
    try:
        end = float(_find(info, ("end_time", "expire_time", "expired_time")))
        expires = datetime.fromtimestamp(end).strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return {"code": code, "title": _text(_find(info, ("title", "icon_text", "customised_label"))),
            "shop": _text(_find(info, ("shop_name", "display_shop_name"))), "discount": discount,
            "cap": _money(_find(info, ("discount_cap", "reward_cap"))),
            "min_spend": _money(_find(info, ("min_spend", "min_amount"))), "expires": expires}


def parse_order(data: dict) -> dict:
    order_id = _find(data, ("order_id", "orderid", "orderId"))
    redirect = _find(data, ("action_redirect_url", "pc_redirect_url"))
    if not order_id and redirect:
        match = re.search(r"(?:[?&]order_?id=|/order/)(\d+)", str(redirect), re.I)
        if match:
            order_id = match.group(1)
    title = _text(_find(data, ("order_status_text", "status_text", "status_label", "status_description", "title")))
    if not title:
        status = _find(data, ("order_status", "shipping_status"))
        if status != "":
            title = f"Mã trạng thái: {status}" if isinstance(status, (int, float)) else _text(status)
    content = _text(_find(data, ("content",)))
    tracking = _text(_find(data, ("tracking_number", "tracking_no", "tracking_code", "waybill",
                                      "shipping_traceno", "shipping_tracking_number")))
    if not tracking:
        match = re.search(r"\bSPX[A-Z0-9]{10,25}\b", (title + " " + content).upper())
        tracking = match.group(0) if match else ""
    products, links = [], []
    for obj in _objects(data):
        name = next((_text(obj[k]) for k in ("item_name", "product_name", "item_title") if obj.get(k)), "")
        if name and name not in products:
            products.append(name)
        item_id, shop_id = obj.get("item_id", obj.get("itemid")), obj.get("shop_id", obj.get("shopid"))
        if item_id and shop_id:
            link = f"https://shopee.vn/product/{shop_id}/{item_id}"
            if link not in links:
                links.append(link)
    return {"order_id": str(order_id), "order_sn": _text(_find(data, ("order_sn", "ordersn", "order_no"))),
            "tracking": tracking, "status": title or content,
            "receiver": _text(_find(data, ("receiver_name", "recipient_name", "consignee_name"))),
            "phone": _text(_find(data, ("receiver_phone", "recipient_phone", "consignee_phone"))),
            "address": _text(_find(data, ("shipping_address", "receiver_address", "recipient_address"))),
            "products": "\n".join(products), "links": "\n".join(links)}


class ShopeeClient:
    ORIGIN = "https://shopee.vn"

    def __init__(self, cookie: str, proxy: str, *, session=None,
                 cancelled: Callable[[], bool] = lambda: False):
        self.cookie = normalize_cookie(cookie)
        self.proxy = normalize_proxy(proxy)
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        self.cancelled = cancelled
        self.deadline = time.monotonic() + 90

    def close(self):
        self.session.close()

    def request(self, path: str, payload: dict | None = None) -> dict:
        if self.cancelled():
            raise ShopeeError("Đã dừng kiểm tra.")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ShopeeError("Hết thời gian kiểm tra tài khoản (90 giây).")
        if not path.startswith("/api/") or "\r" in path or "\n" in path:
            raise ShopeeError("Đường dẫn Shopee không hợp lệ.")
        headers = {"Cookie": self.cookie, "Accept": "application/json", "Origin": self.ORIGIN,
                   "Referer": self.ORIGIN + "/user/purchase/", "X-API-SOURCE": "pc",
                   "X-Shopee-Language": "vi", "X-Requested-With": "XMLHttpRequest",
                   "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"}
        csrf = cookie_value(self.cookie, "csrftoken")
        if csrf:
            headers["x-csrftoken"] = csrf
        try:
            response = self.session.request(
                "POST" if payload is not None else "GET", self.ORIGIN + path,
                json=payload, headers=headers, proxies={"http": self.proxy, "https": self.proxy},
                timeout=(min(5, remaining), min(5, remaining)), allow_redirects=False, stream=True)
        except requests.exceptions.RequestException:
            raise ShopeeError("Không kết nối được qua proxy (kiểm tra proxy, tài khoản proxy và mạng).") from None
        try:
            if response.status_code in {401, 403}:
                raise ShopeeError(f"Shopee từ chối truy cập (HTTP {response.status_code}); cookie/proxy có thể cần xác minh.")
            if not 200 <= response.status_code < 300:
                raise ShopeeError(f"Shopee HTTP {response.status_code}.")
            try:
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=8192):
                    if self.cancelled():
                        raise ShopeeError("Đã dừng kiểm tra.")
                    if time.monotonic() >= self.deadline:
                        raise ShopeeError("Hết thời gian kiểm tra tài khoản (90 giây).")
                    size += len(chunk)
                    if size > 5 * 1024 * 1024:
                        raise ShopeeError("Phản hồi Shopee quá lớn; đã dừng đọc.")
                    chunks.append(chunk)
                body = json.loads(b"".join(chunks))
            except requests.exceptions.RequestException:
                raise ShopeeError("Proxy bị ngắt hoặc hết thời gian đọc phản hồi Shopee.") from None
            except ValueError:
                raise ShopeeError("Shopee không trả JSON; có thể đang yêu cầu xác minh.") from None
            if not isinstance(body, dict):
                raise ShopeeError("Phản hồi Shopee không hợp lệ.")
            error = body.get("error", body.get("error_code", body.get("retcode", 0)))
            if error and str(error) != "0":
                code = str(error) if re.fullmatch(r"-?\d{1,12}", str(error)) else "không xác định"
                if code == "90309999":
                    raise ShopeeError("Shopee yêu cầu xác minh (90309999); không lấy được dữ liệu qua API này.")
                raise ShopeeError(f"Shopee trả lỗi {code}; kiểm tra lại phiên đăng nhập/cookie.")
            data = body.get("data")
            if not isinstance(data, dict):
                raise ShopeeError("Shopee không trả dữ liệu hợp lệ.")
            return data
        finally:
            response.close()

    def vouchers(self) -> tuple[list[dict], str]:
        rows, seen, cursors = [], set(), set()
        cursor = ""
        for _ in range(20):
            try:
                data = self.request("/api/v4/voucher_wallet/get_user_voucher_list", {
                    "exclude_user_voucher_list_type": [], "voucher_status": 1, "voucher_sort_flag": 1,
                    "cursor": cursor, "limit": 50, "addition": ["voucher_microsite_link"],
                    "version": 7, "need_statistics": True, "user_voucher_list_type": 1})
                if not isinstance(data.get("user_voucher_list"), list):
                    raise ShopeeError("Phản hồi voucher thiếu danh sách; chưa xác định được voucher.")
                page = data["user_voucher_list"]
                skipped = False
                for item in page:
                    if not isinstance(item, dict):
                        skipped = True
                        continue
                    row = parse_voucher(item)
                    identity = str(_find(item, ("promotionid", "promotion_id", "voucher_id"))) or row["code"]
                    if row["code"] and identity not in seen:
                        seen.add(identity)
                        rows.append(row)
                    elif not row["code"]:
                        skipped = True
                if skipped:
                    return rows, "Có voucher chưa đọc được theo cấu trúc API hiện tại; danh sách có thể chưa đầy đủ."
                next_cursor = str(data.get("next") or data.get("next_cursor") or "")
                if not next_cursor or not page:
                    return rows, ""
                if next_cursor == cursor or next_cursor in cursors:
                    return rows, "Voucher: Shopee lặp trang; danh sách có thể chưa đầy đủ."
                cursors.add(next_cursor)
                cursor = next_cursor
            except ShopeeError as exc:
                return rows, str(exc)
        return rows, "Voucher: đã lấy 20 trang; danh sách có thể chưa đầy đủ."

    def orders(self) -> tuple[list[dict], str]:
        rows, seen, warning = [], set(), ""
        # Recent order API first; notifications retain useful results if it is blocked.
        try:
            for offset in range(0, 100, 20):
                data = self.request(f"/api/v4/order/get_all_order_and_checkout_list?limit=20&offset={offset}&version=7")
                page = next((obj[key] for obj in _objects(data)
                             for key in ("order_or_checkout_list", "order_list", "details_list", "orders", "order_data")
                             if key in obj and isinstance(obj[key], list)), None)
                if page is None:
                    raise ShopeeError("Phản hồi danh sách đơn chưa được hỗ trợ.")
                for obj in page:
                    if not isinstance(obj, dict):
                        raise ShopeeError("Phản hồi danh sách đơn không hợp lệ.")
                    row = parse_order(obj)
                    key = row["order_id"] or row["order_sn"]
                    if key and key not in seen:
                        rows.append(row)
                        seen.add(key)
                if len(page) < 20:
                    break
            else:
                warning = "Đang hiển thị tối đa 100 đơn gần đây."
        except ShopeeError as exc:
            warning = "Danh sách đơn: " + str(exc)
            try:
                data = self.request("/api/v4/notification/get_notifications?action_cate=4&cursor=&limit=100")
                actions = data.get("actions")
                if not isinstance(actions, list):
                    raise ShopeeError("Phản hồi thông báo thiếu danh sách.")
                for action in actions:
                    row = parse_order(action)
                    key = row["order_id"] or row["order_sn"] or row["tracking"]
                    if key and key not in seen:
                        seen.add(key)
                        rows.append(row)
                warning += " Chỉ bổ sung các đơn tìm thấy trong thông báo gần đây."
            except ShopeeError as exc:
                warning += " Thông báo: " + str(exc)
        # Fetch missing detail only; cap work and keep already obtained results.
        incomplete = [row for row in rows if row["order_id"] and not (row["tracking"] and row["products"])]
        for row in incomplete[:10]:
            try:
                data = self.request("/api/v4/order/get_order_detail?order_id=" + quote(row["order_id"], safe=""))
                detail = parse_order(data)
                for key, value in detail.items():
                    if value:
                        row[key] = value
            except ShopeeError as exc:
                warning = (warning + " Chi tiết đơn: " + str(exc)).strip()
                break
        if len(incomplete) > 10:
            warning += " Chỉ bổ sung chi tiết 10 đơn gần đây mỗi lần kiểm tra."
        return rows, warning.strip()

    def profile(self) -> tuple[str, str]:
        try:
            data = self.request("/api/v4/account/basic/get_account_info")
            # Only this endpoint's root identity belongs to the signed-in user.
            # A recursive search could return a seller/recipient username instead.
            username, user_id = data.get("username"), data.get("userid")
            if (not isinstance(username, str) or not username.strip()
                    or len(username) > 200 or re.search(r"[\x00-\x1f\x7f]", username)
                    or not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0):
                raise ShopeeError("Shopee chưa trả username của tài khoản đăng nhập; kiểm tra lại cookie.")
            return username.strip(), ""
        except ShopeeError as exc:
            return "", str(exc)

    def check(self) -> dict:
        username, profile_error = self.profile()
        vouchers, voucher_error = self.vouchers()
        orders, order_error = self.orders()
        return {"orders": orders, "vouchers": vouchers, "order_error": order_error,
                "voucher_error": voucher_error, "username": username, "profile_error": profile_error,
                "status": "Có cảnh báo" if order_error or voucher_error or profile_error else "Đã kiểm tra"}
