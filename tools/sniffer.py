#!/usr/bin/env python3
"""
Talk to a device at the byte level while investigating a protocol: watch
what it sends, send raw bytes, or send a framed command and decode the
reply. Stop lovingcool-controld first so the port is free.

    uv run python tools/sniffer.py 33c3:7791                     watch incoming bytes
    uv run python tools/sniffer.py 33c3:7791 --command 06        getInfo, reply decoded
    uv run python tools/sniffer.py 33c3:7791 --raw "55 aa 07 00 06 0c 01"
    uv run python tools/sniffer.py 0483:5750 --transport hid --raw "10 00" --log capture.log
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from lovingcool_control.core import protocol
from lovingcool_control.core.device import KNOWN_DEVICES, AIODevice, DeviceError, DeviceSpec, find_serial_port

KNOWN = {(s.vid, s.pid): s for s in KNOWN_DEVICES}


def hexdump(data: bytes, width: int = 16) -> str:
    rows = []
    for at in range(0, len(data), width):
        chunk = data[at:at + width]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        rows.append(f"  {at:04x}  {chunk.hex(' '):<{width * 3}} {text}")
    return "\n".join(rows)


def parse_hex(text: str) -> bytes:
    return bytes.fromhex(text.replace("0x", "").replace(",", " "))


def make_spec(usb_id: str, transport: str, baud: int, dtr: bool) -> DeviceSpec:
    vid, pid = (int(part, 16) for part in usb_id.split(":"))
    known = KNOWN.get((vid, pid))
    if transport == "auto":
        transport = known.transport if known else ("serial" if find_serial_port(vid, pid) else "hid")
    return DeviceSpec(name=known.name if known else usb_id, vid=vid, pid=pid, transport=transport,
                      baudrate=baud or (known.baudrate if known else 2_000_000),
                      dtr=dtr or bool(known and known.dtr))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("device", help="VID:PID in hex, e.g. 33c3:7791")
    ap.add_argument("--transport", choices=("auto", "serial", "hid"), default="auto")
    ap.add_argument("--baud", type=int, default=0, help="serial baud rate (default: from devices.toml, else 2000000)")
    ap.add_argument("--dtr", action="store_true", help="raise DTR / use DSR-DTR flow control")
    ap.add_argument("--raw", metavar="HEX", help="send these bytes as-is")
    ap.add_argument("--command", metavar="KEY", help="send a framed command (hex key), e.g. 06 for getInfo")
    ap.add_argument("--payload", metavar="HEX", default="", help="payload for --command")
    ap.add_argument("--seconds", type=float, default=0, help="stop watching after this long (0 = until Ctrl+C)")
    ap.add_argument("--log", metavar="FILE", help="append everything to a log file too")
    args = ap.parse_args()

    log_file = open(args.log, "a", encoding="utf8") if args.log else None

    def say(line: str) -> None:
        stamped = f"{datetime.now():%H:%M:%S.%f}"[:-3] + "  " + line
        print(stamped)
        if log_file:
            log_file.write(stamped + "\n")
            log_file.flush()

    dev = AIODevice(make_spec(args.device, args.transport, args.baud, args.dtr))
    try:
        dev.connect()
    except DeviceError as e:
        print(f"Couldn't open {args.device}: {e}", file=sys.stderr)
        return 1
    say(f"opened {dev.spec.name} ({dev.spec.transport})")
    try:
        if args.command:
            key = int(args.command, 16)
            say(f"command 0x{key:02x} -> {protocol.build_frame(key, parse_hex(args.payload)).hex(' ')}")
            say(f"reply: {dev.send_command(key, parse_hex(args.payload))}")
            return 0
        if args.raw:
            data = parse_hex(args.raw)
            dev.send_packet(data)
            say(f"TX {len(data)} bytes\n{hexdump(data)}")
        stop_at = time.monotonic() + args.seconds if args.seconds else None
        say("listening (Ctrl+C to stop)")
        while stop_at is None or time.monotonic() < stop_at:
            data = dev.read_packet(timeout_ms=200)
            if data:
                say(f"RX {len(data)} bytes\n{hexdump(data)}")
    except KeyboardInterrupt:
        pass
    except DeviceError as e:
        say(f"error: {e}")
        return 1
    finally:
        dev.disconnect()
        if log_file:
            log_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
