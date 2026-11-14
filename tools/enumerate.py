#!/usr/bin/env python3
"""
lovingcool-control :: USB Device Enumerator
===================================
Run this FIRST with your AIO plugged in.
It lists all USB HID devices and helps you identify VID:PID for the cooler.

Usage:
    python tools/enumerate.py
    python tools/enumerate.py --all          # show non-HID USB devices too
    python tools/enumerate.py --watch        # watch for new devices being plugged in
"""

import argparse
import json
import sys
import time

try:
    import hid
except ImportError:
    print("[ERROR] 'hid' package not found. Install with: pip install hidapi")
    sys.exit(1)

try:
    import usb.core
    import usb.util  # noqa: F401  (availability check)
    HAS_PYUSB = True
except ImportError:
    HAS_PYUSB = False

try:
    import serial.tools.list_ports as list_ports
    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False


# Known AIO / cooler vendor IDs to highlight in output
KNOWN_COOLER_VIDS = {
    0x0483: "STMicroelectronics (common in AIO controllers)",
    0x0416: "Winbond Electronics",
    0x1e71: "NZXT",
    0x1b1c: "Corsair",
    0x2433: "ASUS",
    0x0cf2: "ENE Technology (RGB controllers)",
    0x048d: "Integrated Technology Express",
    0x0c45: "Microdia / SiGma Micro",
    0x28de: "Valve / Steam Controller",
    0x3633: "Possible Lowi/OEM cooler",
    0x16d0: "MCS Electronics",
    0x33c3: "RT-Thread Team (USB-CDC RTOS stack, common in cheap AIO/RGB MCUs)",
}

HIGHLIGHT = "\033[93m"  # yellow
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"


def get_hid_devices() -> list[dict]:
    """Return all enumerated HID devices."""
    return hid.enumerate()


def get_serial_devices() -> list[dict]:
    """
    Return USB-serial (CDC-ACM) devices as dicts shaped like hid.enumerate()
    entries, so they can share formatting/scoring code.

    IMPORTANT: many AIO/RGB controllers built on cheap MCUs (RT-Thread,
    CH340/CH9102, some STM32 USB-CDC stacks) show up here and NEVER show
    up in hid.enumerate() at all — if your cooler isn't in the HID list
    below, check this section before assuming it's a permissions problem.
    """
    if not HAS_PYSERIAL:
        return []
    devices = []
    for p in list_ports.comports():
        if p.vid is None:
            continue
        devices.append({
            "vendor_id": p.vid,
            "product_id": p.pid,
            "manufacturer_string": p.manufacturer,
            "product_string": p.product or p.description,
            "usage_page": 0,
            "usage": 0,
            "path": p.device,
            "_transport": "serial",
        })
    return devices


def format_device(d: dict, highlight: bool = False) -> str:
    vid = d.get("vendor_id", 0)
    pid = d.get("product_id", 0)
    mfr = d.get("manufacturer_string", "") or ""
    prod = d.get("product_string", "") or ""
    usage_page = d.get("usage_page", 0)
    usage = d.get("usage", 0)
    path = d.get("path", b"").decode() if isinstance(d.get("path"), bytes) else str(d.get("path", ""))

    color = HIGHLIGHT if highlight else ""
    tag = f" {GREEN}<<< POSSIBLE AIO CONTROLLER{RESET}" if highlight else ""
    transport = d.get("_transport", "hid")

    lines = [
        f"{color}{BOLD}VID:PID  {RESET}{color}0x{vid:04X}:0x{pid:04X}{RESET}  [{transport}]{tag}",
        f"  Manufacturer : {mfr or DIM+'(none)'+RESET}",
        f"  Product      : {prod or DIM+'(none)'+RESET}",
        f"  Usage Page   : 0x{usage_page:04X}  Usage: 0x{usage:04X}",
        f"  Path         : {DIM}{path}{RESET}",
    ]
    if vid in KNOWN_COOLER_VIDS:
        lines.append(f"  {CYAN}Known VID: {KNOWN_COOLER_VIDS[vid]}{RESET}")
    return "\n".join(lines)


def score_device(d: dict) -> int:
    """Heuristic score — higher = more likely to be an AIO controller."""
    score = 0
    vid = d.get("vendor_id", 0)
    prod = (d.get("product_string", "") or "").lower()
    mfr = (d.get("manufacturer_string", "") or "").lower()
    usage_page = d.get("usage_page", 0)

    aio_keywords = ["cooler", "aio", "pump", "lcd", "liquid", "cool", "lovi", "loving"]
    for kw in aio_keywords:
        if kw in prod or kw in mfr:
            score += 10

    if vid in KNOWN_COOLER_VIDS:
        score += 3

    # Usage page 0xFF00+ = vendor-defined (common in cooler controllers)
    if usage_page >= 0xFF00:
        score += 5

    # Usage page 0x0001 = generic desktop (keyboards/mice, unlikely to be AIO)
    if usage_page == 0x0001:
        score -= 3

    # Serial (CDC-ACM) devices with a real VID/PID are inherently a decent
    # candidate — they're invisible to hid.enumerate(), so if it showed up
    # here at all it's worth a look.
    if d.get("_transport") == "serial":
        score += 3

    return score


def snapshot() -> dict[str, dict]:
    """Return a dict keyed by path for quick diffing (HID + serial)."""
    devices = get_hid_devices() + get_serial_devices()
    return {
        (d.get("path", b"").decode() if isinstance(d.get("path"), bytes) else str(d.get("path", ""))): d
        for d in devices
    }


def watch_mode():
    """Watch for new USB HID or USB-serial devices being plugged in."""
    print(f"{BOLD}Watching for new USB HID / serial devices... (Ctrl+C to stop){RESET}")
    print(f"{DIM}Plug in your AIO cooler now if it isn't already.{RESET}\n")

    before = snapshot()
    try:
        while True:
            time.sleep(1)
            after = snapshot()
            new = {k: v for k, v in after.items() if k not in before}
            removed = {k: v for k, v in before.items() if k not in after}

            for _path, d in new.items():
                vid = d.get("vendor_id", 0)
                pid = d.get("product_id", 0)
                prod = d.get("product_string", "") or "(no name)"
                print(f"{GREEN}[+] CONNECTED{RESET}  VID:PID=0x{vid:04X}:0x{pid:04X}  {prod}")
                print(format_device(d, highlight=score_device(d) > 3))
                print()

            for _path, d in removed.items():
                vid = d.get("vendor_id", 0)
                pid = d.get("product_id", 0)
                prod = d.get("product_string", "") or "(no name)"
                print(f"{RED}[-] REMOVED{RESET}    VID:PID=0x{vid:04X}:0x{pid:04X}  {prod}")

            before = after
    except KeyboardInterrupt:
        print("\nStopped.")


def dump_json(devices: list[dict], path: str):
    """Save enumeration results to JSON for sharing/analysis."""
    serializable = []
    for d in devices:
        entry = dict(d)
        if isinstance(entry.get("path"), bytes):
            entry["path"] = entry["path"].decode(errors="replace")
        serializable.append(entry)
    with open(path, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"\n{GREEN}Saved to {path}{RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="Enumerate USB HID devices to find your Lovi.Cool AIO"
    )
    parser.add_argument("--all", action="store_true", help="Show all devices, not just candidates")
    parser.add_argument("--watch", action="store_true", help="Watch for newly connected devices")
    parser.add_argument("--json", metavar="FILE", help="Dump full enumeration to JSON file")
    parser.add_argument("--vid", type=lambda x: int(x, 16), help="Filter by vendor ID (hex, e.g. 0x0483)")
    args = parser.parse_args()

    if args.watch:
        watch_mode()
        return

    hid_devices = get_hid_devices()
    serial_devices = get_serial_devices()
    devices = hid_devices + serial_devices

    if args.vid:
        devices = [d for d in devices if d.get("vendor_id") == args.vid]

    # Sort by score descending
    devices = sorted(devices, key=score_device, reverse=True)

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  lovingcool-control USB Enumerator{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"  Found {len(hid_devices)} HID device(s), {len(serial_devices)} USB-serial device(s)\n")
    if not HAS_PYSERIAL:
        print(f"  {HIGHLIGHT}[!] pyserial not installed — USB-serial (CDC-ACM) devices won't be shown.{RESET}")
        print("      Many cheap AIO controllers enumerate this way instead of as HID.")
        print("      Install with: pip install pyserial\n")

    shown = 0
    for d in devices:
        score = score_device(d)
        if not args.all and score < 0:
            continue
        is_candidate = score > 3
        print(format_device(d, highlight=is_candidate))
        print()
        shown += 1

    if shown == 0:
        print(f"{RED}No devices matched the filter. Try --all to see everything.{RESET}")

    print(f"{DIM}{'─'*60}{RESET}")
    print(f"\n{CYAN}Next steps:{RESET}")
    print("  1. Find your AIO in the list above (look for the highlighted entry)")
    print("  2. Note the VID:PID (e.g. 0x0483:0x5750)")
    print("  3. Add it to core/device.py under KNOWN_DEVICES")
    print("  4. Run: python tools/sniffer.py --vid 0xXXXX --pid 0xXXXX")
    print(f"\n  If unsure, run: {BOLD}python tools/enumerate.py --watch{RESET}")
    print("  then plug in the AIO and watch what appears.\n")

    if args.json:
        dump_json(get_hid_devices(), args.json)


if __name__ == "__main__":
    main()
