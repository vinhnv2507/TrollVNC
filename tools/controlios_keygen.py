"""Công cụ VENDOR (giữ RIÊNG, không phát cho khách) — tạo license cho ControlIOS.

License buộc theo UDID máy + có hạn dùng, ký số ECDSA P-256 (SHA-256). Máy khách
kiểm bằng khoá CÔNG KHAI nhúng trong daemon; không có khoá riêng thì không chế
được license mới, và sai UDID/hết hạn thì daemon từ chối phục vụ.

Định dạng license (một chuỗi để khách dán vào app):

    <b64url(payload_json)>.<b64url(chữ ký DER)>

payload_json (JSON compact, khoá sắp xếp):
    {"v":1,"udid":"<UDID>","exp":<epoch giây, 0=vĩnh viễn>,"tok":"<b64 32 byte>"}

`tok` là bí mật vận hành: daemon dùng nó làm control token + mật khẩu VNC
("khoá có ích") — thiếu license hợp lệ thì không có tok đúng => tool vô dụng.

Lệnh:
    genkeys                              -> tạo cặp khoá (lưu private PEM + in public)
    issue --udid U --days N [--token T]  -> tạo license
    verify <license> --pub <hex65>       -> kiểm thử tại chỗ (mô phỏng daemon)
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature

PRIV_PEM = Path(__file__).with_name("controlios_private.pem")


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_dec(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def cmd_genkeys(_args) -> None:
    if PRIV_PEM.exists():
        sys.exit(f"Đã có {PRIV_PEM} — xoá đi nếu thật sự muốn tạo khoá mới (sẽ vô "
                 "hiệu mọi license cũ).")
    priv = ec.generate_private_key(ec.SECP256R1())
    PRIV_PEM.write_bytes(priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    pub = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    print(f"Đã lưu khoá riêng: {PRIV_PEM}  (GIỮ BÍ MẬT, đừng đưa cho ai)")
    print(f"\nKhoá công khai (65 byte, nhúng vào daemon):\n  hex: {pub.hex()}")
    print("\n  C array (dán vào trollvncserver.mm):")
    body = ", ".join(f"0x{b:02x}" for b in pub)
    print(f"  static const uint8_t kLicensePubKey[65] = {{{body}}};")


def _load_priv() -> ec.EllipticCurvePrivateKey:
    if not PRIV_PEM.exists():
        sys.exit(f"Chưa có {PRIV_PEM} — chạy `genkeys` trước.")
    return serialization.load_pem_private_key(PRIV_PEM.read_bytes(), password=None)


def cmd_issue(args) -> None:
    udid = args.udid.strip()
    if not udid:
        sys.exit("Thiếu --udid")
    exp = 0 if args.days <= 0 else int(time.time()) + args.days * 86400
    tok = args.token or _b64u(secrets.token_bytes(24))
    payload = {"v": 1, "udid": udid, "exp": exp, "tok": tok}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = _load_priv().sign(payload_bytes, ec.ECDSA(hashes.SHA256()))
    license_str = f"{_b64u(payload_bytes)}.{_b64u(sig)}"
    when = "vĩnh viễn" if exp == 0 else time.strftime("%d/%m/%Y", time.localtime(exp))
    print(f"# UDID   : {udid}")
    print(f"# Hạn    : {when}")
    print(f"# Token  : {tok}   (đặt control_token này ở PC nếu dùng WiFi)")
    print(f"\n{license_str}")


def cmd_verify(args) -> None:
    pub_bytes = bytes.fromhex(args.pub)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)
    try:
        payload_b64, sig_b64 = args.license.split(".")
        payload_bytes = _b64u_dec(payload_b64)
        pub.verify(_b64u_dec(sig_b64), payload_bytes, ec.ECDSA(hashes.SHA256()))
    except (ValueError, InvalidSignature) as exc:
        sys.exit(f"KHÔNG hợp lệ: {exc}")
    payload = json.loads(payload_bytes)
    now = int(time.time())
    expired = payload["exp"] and now > payload["exp"]
    print("Chữ ký: HỢP LỆ")
    print(f"UDID  : {payload['udid']}")
    print(f"Hạn   : {'vĩnh viễn' if not payload['exp'] else time.strftime('%d/%m/%Y', time.localtime(payload['exp']))}"
          + ("  (ĐÃ HẾT HẠN)" if expired else ""))
    print(f"Token : {payload['tok']}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ControlIOS license keygen (vendor)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("genkeys").set_defaults(func=cmd_genkeys)
    p_issue = sub.add_parser("issue")
    p_issue.add_argument("--udid", required=True)
    p_issue.add_argument("--days", type=int, default=30, help="0 = vĩnh viễn")
    p_issue.add_argument("--token", default="")
    p_issue.set_defaults(func=cmd_issue)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("license")
    p_verify.add_argument("--pub", required=True, help="khoá công khai hex 65 byte")
    p_verify.set_defaults(func=cmd_verify)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
