#!/usr/bin/env python3
"""
Check that a connected LCD controller talks the command protocol, and
compare serial settings (DTR flow control on/off).

    uv run python tools/check_device.py                 # info queries only, screen untouched
    uv run python tools/check_device.py --frames 20     # also stream a test pattern (changes the screen)

The port must be free: close lovingcool-control / lovi-ctrl first.
"""

from __future__ import annotations

import argparse
import dataclasses
import statistics
import sys
import time

from PIL import Image, ImageDraw

from lovingcool_control.core.device import KNOWN_DEVICES, AIODevice, DeviceError, find_serial_port
from lovingcool_control.core.lcd import LCDController


def pick_spec(usb_id: str | None):
    for spec in KNOWN_DEVICES:
        if spec.transport != "serial":
            continue
        if usb_id and spec.usb_id != usb_id.lower():
            continue
        if find_serial_port(spec.vid, spec.pid):
            return spec
    return None


def test_pattern(size: tuple[int, int], n: int) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, (20, 20, 30))
    d = ImageDraw.Draw(img)
    x = int((n * 17) % w)
    d.rectangle((x, 0, x + w // 8, h), fill=(230, 80, 40))
    d.text((10, 10), f"lovingcool-control check #{n}", fill=(255, 255, 255))
    return img


def run(spec, queries: int, frames: int) -> bool:
    label = f"dtr={spec.dtr}"
    dev = AIODevice(spec)
    try:
        dev.connect()
    except DeviceError as e:
        print(f"[{label}] connect failed: {e}")
        return False
    ok = True
    try:
        times, info = [], {}
        for _ in range(queries):
            t = time.monotonic()
            try:
                info = dev.get_info()
                times.append(time.monotonic() - t)
            except DeviceError as e:
                print(f"[{label}] get_info failed: {e}")
                ok = False
        if times:
            print(f"[{label}] get_info {len(times)}/{queries} ok, median {statistics.median(times) * 1000:.0f} ms")
            print(f"[{label}] info: {info}")
        if frames and ok:
            lcd = LCDController(dev)
            lcd.refresh_info()
            t = time.monotonic()
            for n in range(frames):
                lcd.send_image(test_pattern(lcd.size, n))
            dt = time.monotonic() - t
            print(f"[{label}] streamed {frames} frames in {dt:.2f}s ({frames / dt:.1f} fps), quality ~{lcd._jpeg_quality_hint}")
            lcd.stop()
            try:
                dev.get_info()
                print(f"[{label}] command channel still answers after streaming")
            except DeviceError as e:
                print(f"[{label}] no answer after streaming: {e}")
                ok = False
    finally:
        dev.disconnect()
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="vid:pid, e.g. 33c3:7791 (default: first connected known device)")
    ap.add_argument("--queries", type=int, default=5)
    ap.add_argument("--frames", type=int, default=0, help="test-pattern frames to stream (changes the screen)")
    ap.add_argument("--dtr", choices=["both", "on", "off"], default="both")
    args = ap.parse_args()

    spec = pick_spec(args.device)
    if spec is None:
        print("No known serial LCD controller is connected.")
        return 1
    print(f"Device: {spec.name} ({spec.usb_id}) at {find_serial_port(spec.vid, spec.pid)}")
    variants = {"on": [True], "off": [False], "both": [False, True]}[args.dtr]
    results = {}
    for dtr in variants:
        results[dtr] = run(dataclasses.replace(spec, dtr=dtr), args.queries, args.frames)
        time.sleep(0.5)
    print("Summary:", ", ".join(f"dtr={k}: {'OK' if v else 'FAILED'}" for k, v in results.items()))
    return 0 if all(results.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
