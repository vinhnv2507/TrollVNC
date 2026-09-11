"""Tight JPEG/fill/copy/palette/gradient decoder, plus ZLib/Raw fallback."""

from __future__ import annotations

import asyncio
import struct
import sys
import unittest
import zlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controlios.vnc.tight import (  # noqa: E402
    ENCODING_RAW,
    ENCODING_TIGHT,
    ENCODING_ZLIB,
    PREFERRED_ENCODINGS,
    QUALITY_LEVEL_6,
    TIGHT_EXPLICIT_FILTER,
    TIGHT_FILL,
    TIGHT_FILTER_COPY,
    TIGHT_FILTER_GRADIENT,
    TIGHT_FILTER_PALETTE,
    TIGHT_JPEG,
    TightDecoder,
    enable_tight,
    pack_compact_length,
    pack_set_encodings,
    rgb_to_mode,
)


def _rect(x: int, y: int, width: int, height: int, encoding: int, payload: bytes) -> bytes:
    return struct.pack(">HHHHi", x, y, width, height, encoding) + payload


def _jpeg_bytes(rgb: np.ndarray, quality: int = 95) -> bytes:
    buf = BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def _deflate(data: bytes, compressor: zlib.compressobj | None = None) -> bytes:
    c = compressor or zlib.compressobj()
    return c.compress(data) + c.flush(zlib.Z_SYNC_FLUSH)


def _tight_jpeg(rgb: np.ndarray) -> bytes:
    payload = _jpeg_bytes(rgb)
    return bytes([TIGHT_JPEG << 4]) + pack_compact_length(len(payload)) + payload


def _tight_fill(r: int, g: int, b: int) -> bytes:
    return bytes([TIGHT_FILL << 4, r, g, b])


def _apply_gradient(rgb: np.ndarray) -> np.ndarray:
    height, width, _ = rgb.shape
    out = np.empty_like(rgb)
    for row in range(height):
        for col in range(width):
            for channel in range(3):
                left = int(rgb[row, col - 1, channel]) if col else 0
                up = int(rgb[row - 1, col, channel]) if row else 0
                upleft = int(rgb[row - 1, col - 1, channel]) if row and col else 0
                pred = min(255, max(0, left + up - upleft))
                out[row, col, channel] = (int(rgb[row, col, channel]) - pred) & 0xFF
    return out


class FakeVideo:
    def __init__(self, width: int = 8, height: int = 8, mode: str = "bgra") -> None:
        self.width = width
        self.height = height
        self.mode = mode
        self.data = None
        self.reader: asyncio.StreamReader | None = None
        self._inflate = zlib.decompressobj()

    def decompress(self, data: bytes) -> bytes:
        return self._inflate.decompress(data)


class Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)


async def _decode(payload: bytes, width: int = 8, height: int = 8, mode: str = "bgra") -> np.ndarray:
    video = FakeVideo(width, height, mode)
    video.reader = asyncio.StreamReader()
    video.reader.feed_data(payload)
    video.reader.feed_eof()
    await TightDecoder(video).read()
    assert video.data is not None
    return video.data


class CompactLengthTest(unittest.TestCase):
    def test_round_trip_boundaries(self) -> None:
        cases = [0, 1, 127, 128, 129, 16383, 16384, 16385, 4194303]
        for n in cases:
            packed = pack_compact_length(n)
            self.assertLessEqual(len(packed), 3)
            recovered = packed[0] & 0x7F
            if packed[0] & 0x80:
                recovered |= (packed[1] & 0x7F) << 7
                if packed[1] & 0x80:
                    recovered |= packed[2] << 14
            self.assertEqual(recovered, n, f"n={n} packed={packed!r}")

    def test_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            pack_compact_length(-1)
        with self.assertRaises(ValueError):
            pack_compact_length(4194304)


class SetEncodingsTest(unittest.TestCase):
    def test_preferred_order_is_tight_then_quality_then_zlib(self) -> None:
        self.assertEqual(PREFERRED_ENCODINGS[0], ENCODING_TIGHT)
        self.assertEqual(PREFERRED_ENCODINGS[1], QUALITY_LEVEL_6)
        self.assertEqual(QUALITY_LEVEL_6, -26)
        self.assertIn(ENCODING_ZLIB, PREFERRED_ENCODINGS)
        self.assertIn(ENCODING_RAW, PREFERRED_ENCODINGS)

    def test_wire_bytes_match_rfb_set_encodings(self) -> None:
        encodings = list(PREFERRED_ENCODINGS)
        packed = pack_set_encodings(encodings)
        kind, pad, count = struct.unpack(">BBH", packed[:4])
        values = list(struct.unpack(">%di" % count, packed[4:]))
        self.assertEqual((kind, pad, count), (2, 0, 4))
        self.assertEqual(values, encodings)
        self.assertEqual(values[1].to_bytes(4, "big", signed=True), b"\xff\xff\xff\xe6")


class RgbModeTest(unittest.TestCase):
    def test_bgra_and_rgba(self) -> None:
        rgb = np.array([[[10, 20, 30]]], dtype=np.uint8)
        bgra = rgb_to_mode(rgb, "bgra")[0, 0]
        rgba = rgb_to_mode(rgb, "rgba")[0, 0]
        argb = rgb_to_mode(rgb, "argb")[0, 0]
        abgr = rgb_to_mode(rgb, "abgr")[0, 0]
        np.testing.assert_array_equal(bgra, [30, 20, 10, 255])
        np.testing.assert_array_equal(rgba, [10, 20, 30, 255])
        np.testing.assert_array_equal(argb, [255, 10, 20, 30])
        np.testing.assert_array_equal(abgr, [255, 30, 20, 10])


class EnableTightTest(unittest.IsolatedAsyncioTestCase):
    async def test_patches_read_and_sends_encodings(self) -> None:
        video = FakeVideo()
        video.reader = asyncio.StreamReader()
        writer = Writer()
        client = SimpleNamespace(writer=writer, video=video)
        enable_tight(client)
        self.assertEqual(bytes(writer.buf), pack_set_encodings(PREFERRED_ENCODINGS))
        self.assertTrue(hasattr(video, "tight_decoder"))
        self.assertEqual(video.read.__func__, video.tight_decoder.read.__func__)
        self.assertIs(video.read.__self__, video.tight_decoder)


class TightDecoderTest(unittest.IsolatedAsyncioTestCase):
    async def test_jpeg_rect_is_approximately_red(self) -> None:
        rgb = np.full((4, 6, 3), [220, 30, 30], dtype=np.uint8)
        data = await _decode(_rect(1, 2, 6, 4, ENCODING_TIGHT, _tight_jpeg(rgb)), width=8, height=8)
        patch = data[2:6, 1:7, :3]
        # BGRA: blue, green, red
        self.assertGreater(int(patch[:, :, 2].mean()), 180)
        self.assertLess(int(patch[:, :, 0].mean()), 80)
        self.assertEqual(int(data[2, 1, 3]), 255)

    async def test_fill_rect_is_exact(self) -> None:
        data = await _decode(_rect(0, 0, 3, 2, ENCODING_TIGHT, _tight_fill(1, 2, 3)), width=3, height=2)
        np.testing.assert_array_equal(data[:, :, 0], 3)  # B
        np.testing.assert_array_equal(data[:, :, 1], 2)  # G
        np.testing.assert_array_equal(data[:, :, 2], 1)  # R
        np.testing.assert_array_equal(data[:, :, 3], 255)

    async def test_copy_uncompressed_below_min_to_compress(self) -> None:
        rgb = np.array([[[9, 8, 7], [6, 5, 4]]], dtype=np.uint8)  # 6 bytes
        payload = bytes([0]) + rgb.tobytes()
        data = await _decode(_rect(0, 0, 2, 1, ENCODING_TIGHT, payload), width=2, height=1)
        np.testing.assert_array_equal(data[0, 0, :3], [7, 8, 9])
        np.testing.assert_array_equal(data[0, 1, :3], [4, 5, 6])

    async def test_copy_zlib_and_explicit_filter(self) -> None:
        rgb = np.arange(4 * 4 * 3, dtype=np.uint8).reshape(4, 4, 3)
        raw = rgb.tobytes()
        compressed = _deflate(raw)
        payload = (
            bytes([TIGHT_EXPLICIT_FILTER << 4, TIGHT_FILTER_COPY])
            + pack_compact_length(len(compressed))
            + compressed
        )
        data = await _decode(_rect(0, 0, 4, 4, ENCODING_TIGHT, payload), width=4, height=4)
        np.testing.assert_array_equal(data[:, :, 2], rgb[:, :, 0])
        np.testing.assert_array_equal(data[:, :, 1], rgb[:, :, 1])
        np.testing.assert_array_equal(data[:, :, 0], rgb[:, :, 2])

    async def test_two_color_palette(self) -> None:
        # 8x2 checker: 0=black, 1=white, packed MSB first.
        palette = bytes([0, 0, 0, 255, 255, 255])
        bits = bytes([0b10101010, 0b01010101])
        payload = bytes([TIGHT_EXPLICIT_FILTER << 4, TIGHT_FILTER_PALETTE, 1]) + palette + bits
        data = await _decode(_rect(0, 0, 8, 2, ENCODING_TIGHT, payload), width=8, height=2)
        row0 = [int(x) for x in data[0, :, 0]]
        row1 = [int(x) for x in data[1, :, 0]]
        self.assertEqual(row0, [255, 0, 255, 0, 255, 0, 255, 0])
        self.assertEqual(row1, [0, 255, 0, 255, 0, 255, 0, 255])

    async def test_indexed_palette(self) -> None:
        palette = bytes([10, 0, 0, 0, 20, 0, 0, 0, 30])
        indices = bytes([0, 1, 2, 1])
        payload = bytes([TIGHT_EXPLICIT_FILTER << 4, TIGHT_FILTER_PALETTE, 2]) + palette + indices
        data = await _decode(_rect(0, 0, 2, 2, ENCODING_TIGHT, payload), width=2, height=2)
        # palette is RGB, framebuffer is BGRA
        self.assertEqual(int(data[0, 0, 2]), 10)  # red
        self.assertEqual(int(data[0, 1, 1]), 20)  # green
        self.assertEqual(int(data[1, 0, 0]), 30)  # blue

    async def test_gradient_filter(self) -> None:
        rgb = np.array(
            [
                [[10, 20, 30], [40, 50, 60], [70, 80, 90]],
                [[11, 21, 31], [41, 51, 61], [71, 81, 91]],
            ],
            dtype=np.uint8,
        )
        diff = _apply_gradient(rgb).tobytes()
        compressed = _deflate(diff)
        payload = (
            bytes([TIGHT_EXPLICIT_FILTER << 4, TIGHT_FILTER_GRADIENT])
            + pack_compact_length(len(compressed))
            + compressed
        )
        data = await _decode(_rect(0, 0, 3, 2, ENCODING_TIGHT, payload), width=3, height=2)
        np.testing.assert_array_equal(data[:, :, 2], rgb[:, :, 0])
        np.testing.assert_array_equal(data[:, :, 1], rgb[:, :, 1])
        np.testing.assert_array_equal(data[:, :, 0], rgb[:, :, 2])

    async def test_raw_and_zlib_fallback(self) -> None:
        raw = np.zeros((2, 2, 4), dtype=np.uint8)
        raw[..., 0] = 9
        raw[..., 1] = 8
        raw[..., 2] = 7
        raw[..., 3] = 1
        data = await _decode(_rect(0, 0, 2, 2, ENCODING_RAW, raw.tobytes()), width=2, height=2)
        np.testing.assert_array_equal(data[..., :3], raw[..., :3])
        np.testing.assert_array_equal(data[..., 3], 255)

        compressed = _deflate(raw.tobytes())
        payload = struct.pack(">I", len(compressed)) + compressed
        data = await _decode(_rect(0, 0, 2, 2, ENCODING_ZLIB, payload), width=2, height=2)
        np.testing.assert_array_equal(data[..., :3], raw[..., :3])

    async def test_unknown_encoding_raises(self) -> None:
        with self.assertRaises(ValueError):
            await _decode(_rect(0, 0, 1, 1, 99, b""), width=1, height=1)

    async def test_zlib_stream_reset_bit(self) -> None:
        video = FakeVideo(4, 4)
        video.reader = asyncio.StreamReader()
        decoder = TightDecoder(video)

        rgb = np.full((4, 4, 3), 40, dtype=np.uint8)
        first = _deflate(rgb.tobytes(), zlib.compressobj())
        payload1 = bytes([0]) + pack_compact_length(len(first)) + first
        video.reader.feed_data(_rect(0, 0, 4, 4, ENCODING_TIGHT, payload1))
        await decoder.read()
        self.assertEqual(int(video.data[0, 0, 0]), 40)

        # Reset stream 0 then send a brand-new zlib wrapper on the same decoder.
        rgb2 = np.full((4, 4, 3), 80, dtype=np.uint8)
        second = _deflate(rgb2.tobytes(), zlib.compressobj())
        payload2 = bytes([0x01]) + pack_compact_length(len(second)) + second
        video.reader.feed_data(_rect(0, 0, 4, 4, ENCODING_TIGHT, payload2))
        video.reader.feed_eof()
        await decoder.read()
        self.assertEqual(int(video.data[0, 0, 0]), 80)


if __name__ == "__main__":
    unittest.main(verbosity=2)
