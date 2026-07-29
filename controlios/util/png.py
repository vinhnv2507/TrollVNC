"""Minimal PNG writer.

Screenshots are written from the asyncio thread, so this deliberately uses
only the standard library — pulling Qt or Pillow into the network layer would
mean either a GUI dependency or another install for users.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def encode_png(rgb: bytes, width: int, height: int, compress_level: int = 6) -> bytes:
    """Encode packed RGB888 rows as an 8-bit truecolour PNG."""

    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"expected {expected} bytes of RGB, got {len(rgb)}")

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)                       # filter type 0 (None)
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), compress_level))
        + chunk(b"IEND", b"")
    )


def write_png(path: Path | str, rgb: bytes, width: int, height: int,
              compress_level: int = 6) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(rgb, width, height, compress_level))
    return path
