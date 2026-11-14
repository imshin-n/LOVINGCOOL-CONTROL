"""
lovingcool-ctl: command-line control of the cooler's screen, through the
lovingcool-controld background service.

    lovingcool-ctl status
    lovingcool-ctl show ~/Pictures/cat.gif
    lovingcool-ctl text "Hello" --fg ffcc00 --size 64
    lovingcool-ctl scene ~/scenes/stats.json
    lovingcool-ctl brightness 60
    lovingcool-ctl off
"""

from __future__ import annotations

import argparse
import json
import sys

from .service.client import ServiceUnavailable, request


def hex_color(value: str) -> list[int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise argparse.ArgumentTypeError(f"expected a colour like ff8800 or #f80, got {value!r}")
    try:
        return [int(v[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"not a hex colour: {value!r}") from e


def percent(value: str) -> int:
    n = int(value)
    if not 0 <= n <= 100:
        raise argparse.ArgumentTypeError("must be 0-100")
    return n


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lovingcool-ctl", description="Control the cooler's screen.",
                                 epilog="Settings are remembered and restored after a restart or replug.")
    ap.add_argument("--json", action="store_true", help="print the raw JSON reply")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    sub.add_parser("status", help="connection, device and what's showing")

    p = sub.add_parser("show", help="show an image, GIF or video file")
    p.add_argument("path")
    p.add_argument("--once", action="store_true", help="play a GIF/video once instead of looping")
    p.add_argument("--rotate", type=int, default=0, metavar="DEG", help="extra rotation in degrees")
    p.add_argument("--portrait", action="store_true", help="compose in portrait orientation")

    p = sub.add_parser("text", help="show a line of text")
    p.add_argument("text")
    p.add_argument("--fg", type=hex_color, default=[255, 255, 255], metavar="HEX")
    p.add_argument("--bg", type=hex_color, default=[0, 0, 0], metavar="HEX")
    p.add_argument("--size", type=int, default=40, help="font size in pixels")
    p.add_argument("--portrait", action="store_true")

    p = sub.add_parser("scene", help="run a Screen Designer scene (.json)")
    p.add_argument("path")

    p = sub.add_parser("brightness", help="backlight brightness, 0-100")
    p.add_argument("value", type=percent)

    sub.add_parser("off", help="blank the screen (remembered until something else is shown)")
    sub.add_parser("reconnect", help="drop and re-open the device connection")
    return ap


def to_request(args: argparse.Namespace) -> dict:
    orientation = "portrait" if getattr(args, "portrait", False) else "landscape"
    if args.cmd == "show":
        return {"cmd": "show", "path": args.path, "loop": not args.once,
                "rotation": args.rotate, "orientation": orientation}
    if args.cmd == "text":
        return {"cmd": "text", "text": args.text, "fg": args.fg, "bg": args.bg,
                "font_size": args.size, "orientation": orientation}
    if args.cmd == "scene":
        return {"cmd": "scene", "path": args.path}
    if args.cmd == "brightness":
        return {"cmd": "brightness", "value": args.value}
    return {"cmd": args.cmd}


def format_status(r: dict) -> str:
    lines = []
    dev = r.get("device")
    if r.get("connected") and dev:
        panel = "x".join(str(n) for n in dev.get("panel") or []) or "?"
        lines.append(f"Device:     {dev['name']} ({dev['usb_id']})")
        lines.append(f"            model {dev.get('model') or '?'}, firmware {dev.get('firmware') or '?'}, {panel}")
    else:
        lines.append("Device:     not connected (waiting for it to appear)")
    content = r.get("content") or "nothing yet"
    lines.append(f"Showing:    {content}{'  [playing]' if r.get('playing') else ''}")
    if r.get("brightness") is not None:
        lines.append(f"Brightness: {r['brightness']}%")
    if r.get("error"):
        lines.append(f"Error:      {r['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reply = request(to_request(args))
    except ServiceUnavailable as e:
        print(f"{e}\nStart it with:  systemctl --user start lovingcool-control", file=sys.stderr)
        return 3
    except (OSError, ValueError) as e:
        print(f"Could not talk to lovingcool-controld: {e}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(reply, indent=2))
    elif not reply.get("ok"):
        print(f"Error: {reply.get('error')}", file=sys.stderr)
    elif args.cmd == "status":
        print(format_status(reply))
    elif args.cmd == "reconnect":
        print("Connected." if reply.get("connected") else "Device not found; will keep trying.")
    else:
        what = reply.get("content") or (f"brightness {reply['brightness']}%" if "brightness" in reply else args.cmd)
        print(f"{'Now showing' if args.cmd != 'brightness' else 'Set'}: {what}" if reply.get("applied")
              else f"Saved: {what} (device not connected; it will be applied when it is)")
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
