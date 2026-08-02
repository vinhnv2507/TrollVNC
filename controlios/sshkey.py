"""Bộ khoá SSH của Control IOS: sinh một lần, dùng cho cả dàn máy.

Khoá riêng nằm ở `config/id_controlios`, không vào git. Cùng một khoá công khai
cài cho mọi máy nên chỉ cần giữ đúng một khoá.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from .config import PROJECT_ROOT

KEY_DIR = PROJECT_ROOT / "config"
PRIVATE_KEY = KEY_DIR / "id_controlios"
PUBLIC_KEY = KEY_DIR / "id_controlios.pub"


def ensure_keypair() -> Tuple[Path, str]:
    """Trả về (đường dẫn khoá riêng, nội dung khoá công khai OpenSSH).

    Sinh mới nếu chưa có. Dùng asyncssh nên không phụ thuộc ssh-keygen của hệ.
    """

    if PRIVATE_KEY.exists() and PUBLIC_KEY.exists():
        return PRIVATE_KEY, PUBLIC_KEY.read_text(encoding="ascii").strip()

    import asyncssh

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    key = asyncssh.generate_private_key("ssh-ed25519", comment="controlios")

    PRIVATE_KEY.write_bytes(key.export_private_key())
    public = key.export_public_key().decode("ascii").strip()
    PUBLIC_KEY.write_text(public + "\n", encoding="ascii")
    try:
        PRIVATE_KEY.chmod(0o600)      # asyncssh cũng chấp nhận nếu bỏ qua được
    except OSError:
        pass
    return PRIVATE_KEY, public
