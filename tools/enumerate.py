#!/usr/bin/env python3
"""
List USB serial and HID devices and point out which could be a cooler's
screen controller, to find the VID:PID of an unsupported model.

    uv run python tools/enumerate.py              likely candidates
    uv run python tools/enumerate.py --all        everything
    uv run python tools/enumerate.py --watch      show devices as they're plugged in / out
    uv run python tools/enumerate.py --json out.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time

from lovingcool_control.core.device import KNOWN_DEVICES, UsbCandidate, find_any_cooler_candidates

KNOWN = {(s.vid, s.pid): s for s in KNOWN_DEVICES}


def describe(c: UsbCandidate) -> str:
    spec = KNOWN.get((c.vendor_id, c.product_id))
    status = f"supported: {spec.name}" if spec else (f"candidate (score {c.score})" if c.score > 2 else "")
    name = " / ".join(x for x in (c.product, c.manufacturer) if x) or "(no name)"
    return f"{c.vendor_id:04x}:{c.product_id:04x}  {c.transport:<6}  {name}  {c.path}  {status}".rstrip()


def snapshot(show_all: bool) -> list[UsbCandidate]:
    devices = find_any_cooler_candidates()
    return devices if show_all else [c for c in devices if c.score > 2 or (c.vendor_id, c.product_id) in KNOWN]


def watch() -> None:
    print("Watching USB devices; plug the cooler's cable in or out (Ctrl+C to stop).")
    seen = {(c.vendor_id, c.product_id, c.path): c for c in find_any_cooler_candidates()}
    try:
        while True:
            time.sleep(1)
            now = {(c.vendor_id, c.product_id, c.path): c for c in find_any_cooler_candidates()}
            for key in now.keys() - seen.keys():
                print("+ " + describe(now[key]))
            for key in seen.keys() - now.keys():
                print("- " + describe(seen[key]))
            seen = now
    except KeyboardInterrupt:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="list every USB serial/HID device, not just likely ones")
    ap.add_argument("--watch", action="store_true", help="report devices as they appear and disappear")
    ap.add_argument("--json", metavar="FILE", help="also write the list to a JSON file")
    ap.add_argument("--vid", type=lambda v: int(v, 16), help="only this vendor ID (hex)")
    args = ap.parse_args()

    if args.watch:
        watch()
        return 0
    devices = [c for c in snapshot(args.all) if args.vid is None or c.vendor_id == args.vid]
    if not devices:
        print("Nothing likely found. Try --all, or --watch and replug the cooler.")
    for c in devices:
        print(describe(c))
    if args.json:
        with open(args.json, "w", encoding="utf8") as f:
            json.dump([dataclasses.asdict(c) for c in devices], f, indent=2)
        print(f"Wrote {len(devices)} device(s) to {args.json}")
    print("\nTo try one, add its VID:PID to devices.toml (see the README, 'Adding a cooler').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
