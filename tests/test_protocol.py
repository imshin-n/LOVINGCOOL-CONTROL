"""Command-channel framing and response parsing (core/device.py), no hardware needed."""

from __future__ import annotations

import json

import pytest

from lovingcool_control.core import device as dev_mod
from lovingcool_control.core.device import KNOWN_DEVICES, AIODevice, DeviceError, DeviceSpec


class FakeSerial:
    """Stands in for serial.Serial: records writes, replays a canned response."""

    def __init__(self, response: bytes = b""):
        self.written = bytearray()
        self.response = response
        self.timeout = 0.1

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, n: int) -> bytes:
        out, self.response = self.response[:n], self.response[n:]
        return out

    def close(self) -> None:
        pass


def make_device(response: bytes = b"") -> tuple[AIODevice, FakeSerial]:
    spec = DeviceSpec(name="test", vid=0x33C3, pid=0x7791, transport="serial")
    d = AIODevice(spec)
    fake = FakeSerial(response)
    d._dev = fake
    d._connected = True
    return d, fake


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(dev_mod.time, "sleep", lambda _s: None)


def test_frame_without_payload_matches_known_bytes():
    # get_info: 55 AA, len=7 (LE), key=06, checksum = 0x55+0xAA+7+0+6 = 0x010C (LE)
    assert AIODevice._build_command_frame(0x06) == bytes.fromhex("55aa0700060c01")


def test_frame_with_payload_length_and_checksum():
    frame = AIODevice._build_command_frame(0x03, bytes([80]))
    assert frame[:2] == b"\x55\xaa"
    assert int.from_bytes(frame[2:4], "little") == len(frame) == 8
    assert frame[4] == 0x03 and frame[5] == 80
    assert int.from_bytes(frame[-2:], "little") == sum(frame[:-2]) & 0xFFFF


def test_get_info_parses_json_response():
    info = {"width": 480, "height": 480, "model": "sc60", "version": "Ver1.0"}
    body = json.dumps({"data": info}).encode()
    d, fake = make_device(AIODevice._build_command_frame(0x06, body))
    assert d.get_info() == info
    assert bytes(fake.written) == AIODevice._build_command_frame(0x06)


def test_non_json_response_is_returned_as_hex():
    d, _ = make_device(AIODevice._build_command_frame(0x06, b"\xff\xfe"))
    assert d.send_command(0x06) == {"raw": "fffe"}


@pytest.mark.parametrize("response", [b"", b"\x00\x01\x02\x03\x04\x05\x06\x07", b"\x55\xaa\x07"])
def test_missing_or_garbled_response_raises(response):
    d, _ = make_device(response)
    with pytest.raises(DeviceError):
        d.send_command(0x06)


def test_brightness_is_clamped_and_needs_no_reply():
    d, fake = make_device()
    d.set_brightness(150)
    assert bytes(fake.written) == AIODevice._build_command_frame(0x03, bytes([100]))


def test_send_when_disconnected_raises():
    d, _ = make_device()
    d._connected = False
    with pytest.raises(DeviceError):
        d.send_packet(b"x")


def test_known_devices_include_7788_and_sc60():
    ids = {(s.vid, s.pid) for s in KNOWN_DEVICES}
    assert {(0x33C3, 0x7788), (0x33C3, 0x7791)} <= ids
