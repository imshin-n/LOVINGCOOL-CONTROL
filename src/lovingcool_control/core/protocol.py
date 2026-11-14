"""
lovingcool-control :: core/protocol.py
======================================
Pure (I/O-free) codec for the LCD controller's command channel.

Frame layout, both directions (33C3:7788 / 7791 family, taken from the
vendor app's main/_baseClass/device.js):

    55 AA | len_lo len_hi | key | payload... | sum_lo sum_hi

    len  = total frame length in bytes (payload + 7)
    sum  = sum of every preceding byte & 0xFFFF, little-endian

Replies carry UTF-8 JSON as the payload. Keeping this module free of
serial/threading code means it can be tested exhaustively without
hardware; core/device.py does the I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

SYNC = b"\x55\xaa"
OVERHEAD = 7                 # sync(2) + len(2) + key(1) + checksum(2)
MAX_FRAME_LEN = 64 * 1024    # sanity bound: anything larger is a corrupt length field


class ProtocolError(Exception):
    pass


@dataclass(frozen=True)
class Frame:
    key: int
    payload: bytes

    def json(self) -> dict:
        """Decode the payload as JSON; non-JSON payloads come back as {"raw": hex}."""
        try:
            value = json.loads(self.payload.decode("utf8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"raw": self.payload.hex()}
        return value if isinstance(value, dict) else {"value": value}


def checksum(data: bytes) -> int:
    return sum(data) & 0xFFFF


def build_frame(key: int, payload: bytes = b"") -> bytes:
    if not 0 <= key <= 0xFF:
        raise ValueError(f"key out of range: {key}")
    total = len(payload) + OVERHEAD
    if total > 0xFFFF:
        raise ValueError(f"payload too large for a 16-bit length: {len(payload)} bytes")
    head = SYNC + total.to_bytes(2, "little") + bytes([key]) + payload
    return head + checksum(head).to_bytes(2, "little")


class FrameParser:
    """
    Incremental parser: feed() it whatever bytes the port produced, in
    any chunking, and it returns every complete, checksum-valid frame.

    Garbage before a sync marker is skipped. A frame with an impossible
    length or a bad checksum is dropped by advancing one byte past its
    sync marker, so a corrupted frame can't wedge the stream. Counts of
    dropped frames are kept for diagnostics.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames: list[Frame] = []
        while True:
            start = self._buf.find(SYNC)
            if start < 0:
                # Keep a trailing 0x55: it may be the first half of the next sync.
                del self._buf[: max(0, len(self._buf) - 1)]
                return frames
            if start:
                del self._buf[:start]
            if len(self._buf) < 4:
                return frames
            length = int.from_bytes(self._buf[2:4], "little")
            if length < OVERHEAD or length > MAX_FRAME_LEN:
                self._drop()
                continue
            if len(self._buf) < length:
                return frames
            raw = bytes(self._buf[:length])
            if checksum(raw[:-2]) != int.from_bytes(raw[-2:], "little"):
                self._drop()
                continue
            del self._buf[:length]
            frames.append(Frame(key=raw[4], payload=raw[5:-2]))

    def _drop(self) -> None:
        self.dropped += 1
        del self._buf[:1]

    def reset(self) -> None:
        self._buf.clear()

    @property
    def pending(self) -> int:
        return len(self._buf)
