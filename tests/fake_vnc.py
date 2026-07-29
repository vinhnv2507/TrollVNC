"""A minimal RFB 3.8 server used to test Control IOS without real phones.

Speaks exactly the subset asyncvnc needs: security type None, BGRA pixel
format, and ZLib-encoded framebuffer updates. It also records the pointer and
key events it receives, so input forwarding can be asserted.
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Tuple

# 16-byte PIXEL_FORMAT matching asyncvnc's 'bgra' mode, so the client does not
# need to renegotiate with SetPixelFormat.
PIXEL_FORMAT = bytes([32, 24, 0, 1]) + struct.pack(">HHH", 255, 255, 255) + \
    bytes([16, 8, 0]) + b"\x00\x00\x00"


@dataclass
class FakeVncServer:
    width: int = 375
    height: int = 667
    name: str = "fake-iphone"

    pointer_events: List[Tuple[int, int, int]] = field(default_factory=list)
    key_events: List[Tuple[int, int]] = field(default_factory=list)
    update_requests: int = 0
    connections: int = 0

    _server: asyncio.AbstractServer | None = None
    _writers: set = field(default_factory=set)
    port: int = 0

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, host, port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Drop live clients too — otherwise wait_closed() blocks on their
        handler tasks, which is exactly what a phone going away looks like."""

        if not self._server:
            return
        self._server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        self._writers.clear()
        await self._server.wait_closed()
        self._server = None

    # ------------------------------------------------------------------ client

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        compressor = zlib.compressobj()
        frame_index = 0
        try:
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            await reader.readexactly(12)

            writer.write(bytes([1, 1]))            # one security type: None
            await writer.drain()
            await reader.readexactly(1)            # chosen type
            writer.write(struct.pack(">I", 0))     # SecurityResult: OK
            await writer.drain()

            await reader.readexactly(1)            # ClientInit
            writer.write(
                struct.pack(">HH", self.width, self.height)
                + PIXEL_FORMAT
                + struct.pack(">I", len(self.name))
                + self.name.encode()
            )
            await writer.drain()

            while True:
                message = await reader.readexactly(1)
                kind = message[0]

                if kind == 0:                      # SetPixelFormat
                    await reader.readexactly(19)
                elif kind == 2:                    # SetEncodings
                    await reader.readexactly(1)
                    count = struct.unpack(">H", await reader.readexactly(2))[0]
                    await reader.readexactly(4 * count)
                elif kind == 3:                    # FramebufferUpdateRequest
                    await reader.readexactly(9)
                    self.update_requests += 1
                    frame_index += 1
                    writer.write(self._frame(compressor, frame_index))
                    await writer.drain()
                elif kind == 4:                    # KeyEvent
                    payload = await reader.readexactly(7)
                    down, _, keysym = struct.unpack(">BHI", payload)
                    self.key_events.append((down, keysym))
                elif kind == 5:                    # PointerEvent
                    buttons, x, y = struct.unpack(">BHH", await reader.readexactly(5))
                    self.pointer_events.append((buttons, x, y))
                elif kind == 6:                    # ClientCutText
                    await reader.readexactly(3)
                    length = struct.unpack(">I", await reader.readexactly(4))[0]
                    await reader.readexactly(length)
                else:
                    raise ValueError(f"unexpected client message {kind}")
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _frame(self, compressor, index: int) -> bytes:
        """A full-screen rect whose blue channel encodes the frame index."""

        pixels = bytearray(self.width * self.height * 4)
        pixels[0::4] = bytes([index % 256]) * (self.width * self.height)   # blue
        payload = compressor.compress(bytes(pixels)) + compressor.flush(zlib.Z_SYNC_FLUSH)
        return (
            b"\x00\x00"                                   # FramebufferUpdate, padding
            + struct.pack(">H", 1)                        # one rect
            + struct.pack(">HHHH", 0, 0, self.width, self.height)
            + struct.pack(">i", 6)                        # ZLib encoding
            + struct.pack(">I", len(payload))
            + payload
        )
