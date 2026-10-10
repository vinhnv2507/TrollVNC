from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import UnsupportedAlgorithm

PUBLIC_KEY_SHA256 = 'b72ea6fbbbadbdb49a2c8ec28dfffd48dea9a64b5109ce084376e5843f427772'


class KeygenError(ValueError):
    pass


def load_signer(path: Path, password: str = '', expected: str = PUBLIC_KEY_SHA256):
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=password.encode() if password else None)
    except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
        raise KeygenError('Không đọc được khoá ký. Kiểm tra file PEM và mật khẩu nếu có.') from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise KeygenError('Khoá ký phải là ECDSA P-256.')
    fingerprint = hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).hexdigest()
    if fingerprint != expected:
        raise KeygenError('Khoá ký không khớp ControlIOS hiện tại. Chọn đúng controlios_private.pem đã dùng trước đây.')
    return key


def validate_token(token: str) -> str:
    token = token.strip()
    if not token or len(token) > 512 or not token.isascii() or any(c.isspace() or ord(c)<33 or ord(c)==127 for c in token):
        raise KeygenError('Cần token kết nối của Manager; token không được chứa khoảng trắng.')
    return token


def parse_devices(text: str) -> list[tuple[str, str]]:
    devices, seen = [], set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        name, separator, udid = line.strip().partition('|')
        if not separator:
            udid, name = name, ''
        name, udid = name.strip(), udid.strip()
        if not re.fullmatch(r'(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16})', udid):
            raise KeygenError(f'Dòng {number}: UDID không hợp lệ. Dùng UDID từ Manager, không dùng số serial.')
        if udid.lower() in seen:
            raise KeygenError(f'Dòng {number}: UDID bị trùng.')
        seen.add(udid.lower())
        devices.append((name, udid))
    if not devices:
        raise KeygenError('Nhập ít nhất một UDID.')
    return devices


@dataclass(frozen=True)
class IssuedKey:
    name: str
    udid: str
    expiry: int
    license: str


def issue_keys(signer, text: str, token: str, expiry: int, now: int | None = None) -> list[IssuedKey]:
    devices = parse_devices(text)
    token = validate_token(token)
    now = int(time.time()) if now is None else now
    if isinstance(expiry, bool) or not isinstance(expiry, int) or expiry < 0 or (expiry != 0 and expiry <= now):
        raise KeygenError('Ngày hết hạn phải ở tương lai.')
    results = []
    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')
    for name, udid in devices:
        payload = json.dumps({'v':1,'udid':udid,'exp':expiry,'tok':token}, sort_keys=True, separators=(',',':')).encode()
        signature = signer.sign(payload, ec.ECDSA(hashes.SHA256()))
        results.append(IssuedKey(name, udid, expiry, b64(payload)+'.'+b64(signature)))
    return results


def load_manager(path: Path) -> tuple[str, list[dict]]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        token = validate_token(data['settings'].get('control_token', ''))
        devices = data.get('devices', [])
        if not isinstance(devices, list) or any(not isinstance(d, dict) for d in devices):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise KeygenError('Không đọc được cấu hình Manager hoặc cấu hình chưa có token kết nối.') from None
    return token, [dict(d, control_port=d.get('control_port') or data['settings'].get('control_port') or 46752) for d in devices]


def read_udid(host: str, port: int, token: str) -> str:
    try:
        ipaddress.ip_address(host.strip())
    except ValueError:
        raise KeygenError('Nhập địa chỉ IP của iPhone.') from None
    token = validate_token(token)
    if not 1 <= port <= 65535:
        raise KeygenError('Cổng kết nối không hợp lệ.')
    try:
        with socket.create_connection((host.strip(), port), timeout=6) as connection:
            connection.sendall(f'auth {token} license\n'.encode('ascii'))
            data = bytearray()
            deadline = time.monotonic()+8
            while b'\n' not in data and len(data) < 16384:
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                chunk = connection.recv(1024)
                if not chunk:
                    break
                data.extend(chunk)
        parts = bytes(data).decode('utf-8').strip().split()
    except (OSError, UnicodeError):
        raise KeygenError('Không đọc được UDID qua LAN. Kiểm tra IP, cổng và token kết nối.') from None
    if parts[:2] == ['ERR', 'Unauthorized']:
        raise KeygenError('Token không đúng với thiết bị này.')
    if len(parts)<2 or parts[0]!='OK' or parts[1] not in ('valid','trial','invalid'):
        raise KeygenError('iPhone chưa hỗ trợ lấy UDID. Cập nhật ControlIOS rồi thử lại.')
    udid = next((p[5:] for p in parts[2:] if p.startswith('udid=')), '')
    parse_devices(udid)
    return udid
