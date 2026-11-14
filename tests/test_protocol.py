"""Command-channel codec (core/protocol.py): pure functions, no I/O."""

from __future__ import annotations

import json
import random

import pytest

from lovingcool_control.core.protocol import Frame, FrameParser, build_frame


def test_frame_without_payload_matches_known_bytes():
    # get_info: 55 AA, len=7 (LE), key=06, checksum = 0x55+0xAA+7+0+6 = 0x010C (LE)
    assert build_frame(0x06) == bytes.fromhex("55aa0700060c01")


def test_frame_with_payload_length_and_checksum():
    frame = build_frame(0x03, bytes([80]))
    assert frame[:2] == b"\x55\xaa"
    assert int.from_bytes(frame[2:4], "little") == len(frame) == 8
    assert frame[4] == 0x03 and frame[5] == 80
    assert int.from_bytes(frame[-2:], "little") == sum(frame[:-2]) & 0xFFFF


@pytest.mark.parametrize("key", [-1, 256])
def test_bad_key_rejected(key):
    with pytest.raises(ValueError):
        build_frame(key)


def test_parser_round_trip():
    body = json.dumps({"data": {"width": 480}}).encode()
    assert FrameParser().feed(build_frame(0x06, body)) == [Frame(0x06, body)]


def test_parser_handles_any_chunking():
    stream = build_frame(0x06, b'{"a":1}') + build_frame(0x11) + build_frame(0x03, b"\x50")
    rng = random.Random(1)
    for _ in range(50):
        parser, got, i = FrameParser(), [], 0
        while i < len(stream):
            n = rng.randint(1, 6)
            got += parser.feed(stream[i:i + n])
            i += n
        assert [f.key for f in got] == [0x06, 0x11, 0x03]
        assert parser.pending == 0


def test_parser_skips_leading_garbage_and_split_sync():
    parser = FrameParser()
    assert parser.feed(b"\x00\x13\x37\x55") == []          # trailing 0x55 may start a sync
    assert parser.feed(build_frame(0x06)[1:]) == [Frame(0x06, b"")]


def test_parser_drops_bad_checksum_and_recovers():
    bad = bytearray(build_frame(0x06, b"xyz"))
    bad[-1] ^= 0xFF
    parser = FrameParser()
    assert parser.feed(bytes(bad) + build_frame(0x11)) == [Frame(0x11, b"")]
    assert parser.dropped >= 1


def test_parser_drops_impossible_length():
    parser = FrameParser()
    assert parser.feed(b"\x55\xaa\x02\x00" + build_frame(0x06)) == [Frame(0x06, b"")]


def test_parser_waits_for_incomplete_frame():
    frame = build_frame(0x06, b"0123456789")
    parser = FrameParser()
    assert parser.feed(frame[:-1]) == []
    assert parser.feed(frame[-1:]) == [Frame(0x06, b"0123456789")]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b'{"data":{"w":1}}', {"data": {"w": 1}}), (b"\xff\xfe", {"raw": "fffe"}), (b"[1,2]", {"value": [1, 2]})],
)
def test_frame_json(payload, expected):
    assert Frame(0x06, payload).json() == expected
