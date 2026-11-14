"""AIODevice command handling, locking and the device registry, using a fake serial port."""

from __future__ import annotations

import json
import threading

import pytest

from lovingcool_control.core.device import (
    KNOWN_DEVICES,
    DeviceError,
    load_device_specs,
    parse_device_specs,
)
from lovingcool_control.core.protocol import build_frame


def test_get_info_parses_reply_and_sets_panel_size(make_device):
    info = {"width": 480, "height": 320, "model": "txm818-7inch", "version": "Ver1.0"}
    d, fake = make_device(b"\x00junk" + build_frame(0x06, json.dumps({"data": info}).encode()))
    assert d.panel_size == (480, 480)            # spec fallback before asking
    assert d.get_info() == info
    assert d.panel_size == (480, 320)            # the device's answer wins
    assert bytes(fake.written) == build_frame(0x06)
    assert fake.flushed_input == 1               # stale input dropped before the command


def test_non_json_reply_returned_as_hex(make_device):
    d, _ = make_device(build_frame(0x06, b"\xff\xfe"))
    assert d.send_command(0x06) == {"raw": "fffe"}


@pytest.mark.parametrize("response", [b"", b"\x00\x01\x02\x03\x04\x05\x06\x07", b"\x55\xaa\x07"])
def test_missing_or_garbled_reply_raises(make_device, response):
    d, _ = make_device(response)
    with pytest.raises(DeviceError):
        d.send_command(0x06, timeout_ms=1)


def test_brightness_is_clamped_and_needs_no_reply(make_device):
    d, fake = make_device()
    d.set_brightness(150)
    assert bytes(fake.written) == build_frame(0x03, bytes([100]))


def test_send_when_disconnected_raises(make_device):
    d, _ = make_device()
    d._connected = False
    with pytest.raises(DeviceError):
        d.send_packet(b"x")


def test_exclusive_blocks_other_writers(make_device):
    d, fake = make_device()
    entered = threading.Event()
    other_done = threading.Event()

    def other():
        entered.wait()
        d.send_packet(b"OTHER")
        other_done.set()

    t = threading.Thread(target=other)
    t.start()
    with d.exclusive():
        d.send_packet(b"A")
        entered.set()
        assert not other_done.wait(0.1)          # blocked while we hold the device
        d.send_packet(b"B")
    t.join(1)
    assert fake.writes == [b"A", b"B", b"OTHER"]


def test_builtin_registry_has_both_known_panels():
    by_id = {(s.vid, s.pid): s for s in KNOWN_DEVICES}
    assert by_id[(0x33C3, 0x7788)].dtr is True and by_id[(0x33C3, 0x7788)].verified
    sc60 = by_id[(0x33C3, 0x7791)]
    assert (sc60.lcd_width, sc60.lcd_height, sc60.lcd_color_format) == (480, 480, "JPEG")


def test_user_file_overrides_and_extends(tmp_path):
    user = tmp_path / "devices.toml"
    user.write_text(
        '[[device]]\nname = "My SC60"\nvid = 0x33c3\npid = 0x7791\ntransport = "serial"\ndtr = true\n'
        '[[device]]\nname = "Other"\nvid = 0x1234\npid = 0x5678\n'
    )
    specs = {(s.vid, s.pid): s for s in load_device_specs(user)}
    assert specs[(0x33C3, 0x7791)].name == "My SC60" and specs[(0x33C3, 0x7791)].dtr is True
    assert (0x1234, 0x5678) in specs and (0x33C3, 0x7788) in specs


def test_broken_user_file_is_ignored(tmp_path):
    user = tmp_path / "devices.toml"
    user.write_text("[[device]]\nname = 'x'\nvid = 1\npid = 2\ntypo_field = 3\n")
    assert {(s.vid, s.pid) for s in load_device_specs(user)} == {(s.vid, s.pid) for s in KNOWN_DEVICES}


def test_unknown_field_is_an_error():
    with pytest.raises(ValueError, match="typo"):
        parse_device_specs("[[device]]\nname='x'\nvid=1\npid=2\ntypo=1\n")
