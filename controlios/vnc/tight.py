"""Tight (RFB encoding 7) for TrollVNC.

asyncvnc only asks for ZLib-raw 32bpp. TrollVNC already links turbojpeg and
will send Tight JPEG once the client lists encoding 7 plus a QualityLevel.
A full-dirty Shopee frame as JPEG is a fraction of the zlib-raw size, which
is what makes remote dragging usable on a busy WiFi farm.
"""

from __future__ import annotations

import struct
import zlib
from io import BytesIO
from typing import List

import numpy as np
from PIL import Image

ENCODING_RAW = 0
ENCODING_ZLIB = 6
ENCODING_TIGHT = 7
QUALITY_LEVEL_6 = -26  # rfbEncodingQualityLevel6 = 0xFFFFFFE6

TIGHT_EXPLICIT_FILTER = 0x04
TIGHT_FILL = 0x08
TIGHT_JPEG = 0x09
TIGHT_FILTER_COPY = 0
TIGHT_FILTER_PALETTE = 1
TIGHT_FILTER_GRADIENT = 2
TIGHT_MIN_TO_COMPRESS = 12

PREFERRED_ENCODINGS: tuple[int, ...] = (
    ENCODING_TIGHT,
    QUALITY_LEVEL_6,
    ENCODING_ZLIB,
    ENCODING_RAW,
)


def pack_set_encodings(encodings: List[int] | tuple[int, ...]) -> bytes:
    encodings = list(encodings)
    return struct.pack(">BBH" + "i" * len(encodings), 2, 0, len(encodings), *encodings)


def pack_compact_length(n: int) -> bytes:
    """1-3 byte Tight length (values 0..4194303)."""

    n = int(n)
    if n < 0 or n > 4194303:
        raise ValueError(f"Tight length out of range: {n}")
    if n < 128:
        return bytes([n])
    if n < 16384:
        return bytes([0x80 | (n & 0x7F), (n >> 7) & 0x7F])
    return bytes([0x80 | (n & 0x7F), 0x80 | ((n >> 7) & 0x7F), (n >> 14) & 0xFF])


def rgb_to_mode(rgb: np.ndarray, mode: str) -> np.ndarray:
    """HxWx3 RGB uint8 -> HxWx4 in the asyncvnc pixel mode."""

    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    height, width, _ = rgb.shape
    out = np.empty((height, width, 4), dtype=np.uint8)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    if mode == "bgra":
        out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = blue, green, red, 255
    elif mode == "rgba":
        out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = red, green, blue, 255
    elif mode == "argb":
        out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = 255, red, green, blue
    elif mode == "abgr":
        out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = 255, blue, green, red
    else:
        out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = red, green, blue, 255
    return out


def enable_tight(client) -> None:
    """Prefer Tight JPEG; keep ZLib/Raw so fake servers and old daemons still work."""

    video = client.video
    decoder = TightDecoder(video)
    video.read = decoder.read  # type: ignore[method-assign]
    video.tight_decoder = decoder  # type: ignore[attr-defined]
    client.writer.write(pack_set_encodings(PREFERRED_ENCODINGS))


class TightDecoder:
    def __init__(self, video) -> None:
        self.video = video
        self._zlibs = [zlib.decompressobj() for _ in range(4)]

    async def read(self) -> None:
        reader = self.video.reader
        x = int.from_bytes(await reader.readexactly(2), "big")
        y = int.from_bytes(await reader.readexactly(2), "big")
        width = int.from_bytes(await reader.readexactly(2), "big")
        height = int.from_bytes(await reader.readexactly(2), "big")
        encoding = int.from_bytes(await reader.readexactly(4), "big")

        if encoding == ENCODING_RAW:
            data = await reader.readexactly(height * width * 4)
            self._blit_native(x, y, width, height, data)
            return
        if encoding == ENCODING_ZLIB:
            length = int.from_bytes(await reader.readexactly(4), "big")
            data = self.video.decompress(await reader.readexactly(length))
            self._blit_native(x, y, width, height, data)
            return
        if encoding != ENCODING_TIGHT:
            raise ValueError(encoding)
        await self._read_tight(x, y, width, height)

    def _ensure(self) -> np.ndarray:
        video = self.video
        if video.data is None:
            video.data = np.zeros((video.height, video.width, 4), dtype=np.uint8)
        return video.data

    def _blit_native(self, x: int, y: int, width: int, height: int, data: bytes) -> None:
        buf = self._ensure()
        buf[y:y + height, x:x + width] = np.ndarray((height, width, 4), "B", data)
        buf[y:y + height, x:x + width, self.video.mode.index("a")] = 255

    def _blit_rgb(self, x: int, y: int, rgb: np.ndarray) -> None:
        packed = rgb_to_mode(rgb, self.video.mode)
        buf = self._ensure()
        height, width = packed.shape[:2]
        buf[y:y + height, x:x + width] = packed

    async def _read_compact(self) -> int:
        reader = self.video.reader
        b0 = (await reader.readexactly(1))[0]
        length = b0 & 0x7F
        if b0 & 0x80:
            b1 = (await reader.readexactly(1))[0]
            length |= (b1 & 0x7F) << 7
            if b1 & 0x80:
                b2 = (await reader.readexactly(1))[0]
                length |= b2 << 14
        return length

    async def _read_tight(self, x: int, y: int, width: int, height: int) -> None:
        ctl = (await self.video.reader.readexactly(1))[0]
        for index in range(4):
            if ctl & (1 << index):
                self._zlibs[index] = zlib.decompressobj()
        kind = ctl >> 4
        if width <= 0 or height <= 0:
            if kind == TIGHT_FILL:
                await self.video.reader.readexactly(3)
            elif kind == TIGHT_JPEG:
                length = await self._read_compact()
                if length:
                    await self.video.reader.readexactly(length)
            return
        if kind == TIGHT_FILL:
            await self._fill(x, y, width, height)
            return
        if kind == TIGHT_JPEG:
            await self._jpeg(x, y, width, height)
            return
        if kind & 0x08:
            raise ValueError(f"unsupported Tight compression {kind}")
        await self._basic(kind, x, y, width, height)

    async def _fill(self, x: int, y: int, width: int, height: int) -> None:
        pixel = await self.video.reader.readexactly(3)
        rgb = np.empty((height, width, 3), dtype=np.uint8)
        rgb[:, :] = np.frombuffer(pixel, dtype=np.uint8)
        self._blit_rgb(x, y, rgb)

    async def _jpeg(self, x: int, y: int, width: int, height: int) -> None:
        length = await self._read_compact()
        payload = await self.video.reader.readexactly(length)
        with Image.open(BytesIO(payload)) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if rgb.shape[1] != width or rgb.shape[0] != height:
            rgb = np.asarray(
                Image.fromarray(rgb).resize((width, height), Image.Resampling.NEAREST),
                dtype=np.uint8,
            )
        self._blit_rgb(x, y, rgb)

    async def _basic(self, kind: int, x: int, y: int, width: int, height: int) -> None:
        filt = TIGHT_FILTER_COPY
        if kind & TIGHT_EXPLICIT_FILTER:
            filt = (await self.video.reader.readexactly(1))[0]
        stream_id = kind & 0x03
        if filt == TIGHT_FILTER_COPY:
            raw = await self._read_pixels(stream_id, width * height * 3)
            rgb = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
            self._blit_rgb(x, y, rgb)
            return
        if filt == TIGHT_FILTER_PALETTE:
            await self._palette(stream_id, x, y, width, height)
            return
        if filt == TIGHT_FILTER_GRADIENT:
            raw = await self._read_pixels(stream_id, width * height * 3)
            rgb = _undo_gradient(
                np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
            )
            self._blit_rgb(x, y, rgb)
            return
        raise ValueError(f"unsupported Tight filter {filt}")

    async def _palette(self, stream_id: int, x: int, y: int, width: int, height: int) -> None:
        count = (await self.video.reader.readexactly(1))[0] + 1
        palette = np.frombuffer(
            await self.video.reader.readexactly(count * 3), dtype=np.uint8
        ).reshape(count, 3)
        if count <= 2:
            row_bytes = (width + 7) // 8
            raw = await self._read_pixels(stream_id, row_bytes * height)
            bits = np.frombuffer(raw, dtype=np.uint8).reshape(height, row_bytes)
            indices = np.unpackbits(bits, axis=1, bitorder="big")[:, :width]
        else:
            raw = await self._read_pixels(stream_id, width * height)
            indices = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
        self._blit_rgb(x, y, palette[indices])

    async def _read_pixels(self, stream_id: int, expected: int) -> bytes:
        if expected == 0:
            return b""
        if expected < TIGHT_MIN_TO_COMPRESS:
            return await self.video.reader.readexactly(expected)
        length = await self._read_compact()
        chunk = await self.video.reader.readexactly(length)
        data = self._zlibs[stream_id].decompress(chunk)
        while len(data) < expected:
            more = self._zlibs[stream_id].decompress(b"")
            if not more:
                break
            data += more
        if len(data) != expected:
            raise ValueError(f"Tight zlib size {len(data)} != {expected}")
        return data


def _undo_gradient(diff: np.ndarray) -> np.ndarray:
    """Reconstruct RGB after the Tight gradient filter (see rfbproto NOTE)."""

    height, width, _ = diff.shape
    out = np.empty_like(diff)
    for row in range(height):
        for col in range(width):
            for channel in range(3):
                left = int(out[row, col - 1, channel]) if col else 0
                up = int(out[row - 1, col, channel]) if row else 0
                upleft = int(out[row - 1, col - 1, channel]) if row and col else 0
                pred = left + up - upleft
                if pred < 0:
                    pred = 0
                elif pred > 255:
                    pred = 255
                out[row, col, channel] = (int(diff[row, col, channel]) + pred) & 0xFF
    return out
