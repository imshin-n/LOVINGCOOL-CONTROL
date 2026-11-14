from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from lovingcool_control.core import device as dev_mod
from lovingcool_control.core.device import AIODevice, DeviceSpec


class FakeSerial:
    """Stands in for serial.Serial: records writes, hands out queued reply bytes."""

    def __init__(self, response: bytes = b""):
        self.written = bytearray()
        self.writes: list[bytes] = []
        self.response = bytearray(response)
        self.timeout = 0.1
        self.flushed_input = 0
        self.lock = threading.Lock()

    def write(self, data: bytes) -> int:
        with self.lock:
            self.written += data
            self.writes.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.flushed_input += 1

    def read(self, n: int) -> bytes:
        out = bytes(self.response[:n])
        del self.response[:n]
        return out

    def close(self) -> None:
        pass


@pytest.fixture
def make_device(monkeypatch):
    """Factory: a connected serial AIODevice backed by FakeSerial, with sleeps skipped."""
    # Replace only device.py's view of `time`; patching time.sleep itself would affect everything.
    monkeypatch.setattr(dev_mod, "time", SimpleNamespace(sleep=lambda _s: None, monotonic=time.monotonic))

    def factory(response: bytes = b"", **spec_kw) -> tuple[AIODevice, FakeSerial]:
        spec = DeviceSpec(**{"name": "test", "vid": 0x33C3, "pid": 0x7791, "transport": "serial",
                             "lcd_width": 480, "lcd_height": 480, "lcd_color_format": "JPEG", **spec_kw})
        d = AIODevice(spec)
        fake = FakeSerial(response)
        d._dev = fake
        d._connected = True
        return d, fake

    return factory
