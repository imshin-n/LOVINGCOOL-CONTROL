#!/usr/bin/env python3
"""
lovingcool-control :: tools/protocol_diff.py
======================================
Diff two sniffer log files to isolate which bytes changed between
two different device actions (e.g. "static red" vs "static blue").
This is the core technique for reverse-engineering the RGB/LCD protocol.

Workflow:
  1. Set device to state A in the official software → capture with sniffer.py → save log_a.log
  2. Change ONE thing (e.g. switch from red to blue) → capture → save log_b.log
  3. Run: python tools/protocol_diff.py log_a.log log_b.log

  Bytes that changed between captures are highlighted. After a few rounds
  you'll know which byte offset controls colour, mode, brightness, etc.

Usage:
    python tools/protocol_diff.py capture_a.log capture_b.log
    python tools/protocol_diff.py capture_a.log capture_b.log --direction OUT
    python tools/protocol_diff.py capture_a.log capture_b.log --min-packets 3
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"


def parse_log(path: str, direction_filter: str | None = None) -> list[tuple[str, bytes]]:
    """
    Parse a sniffer log file into a list of (direction, packet_bytes) tuples.
    The log format is:
        [HH:MM:SS.mmm] ← #0001  IN  64 bytes
          0000  AA BB CC ...  |...|
    """
    packets: list[tuple[str, bytes]] = []
    current_dir: str | None = None
    current_bytes: list[int] = []
    hex_re = re.compile(r"^\s+[0-9A-F]{4}\s+((?:[0-9A-F]{2}\s*)+)\s+\|")

    with open(path) as f:
        for line in f:
            # Header line
            if "IN" in line or "OUT" in line:
                if current_bytes:
                    if not direction_filter or current_dir == direction_filter:
                        packets.append((current_dir, bytes(current_bytes)))
                    current_bytes = []
                current_dir = "IN" if " IN " in line else "OUT"
                continue
            m = hex_re.match(line)
            if m:
                hex_str = m.group(1).strip()
                current_bytes.extend(int(x, 16) for x in hex_str.split())

    if current_bytes and current_dir:
        if not direction_filter or current_dir == direction_filter:
            packets.append((current_dir, bytes(current_bytes)))

    return packets


def align_packets(a: list[tuple[str, bytes]], b: list[tuple[str, bytes]]) -> list[tuple]:
    """
    Pair up packets from A and B by position and direction.
    Returns list of (direction, bytes_a, bytes_b) tuples.
    """
    result = []
    for i, (dir_a, pkt_a) in enumerate(a):
        if i < len(b):
            dir_b, pkt_b = b[i]
            if dir_a == dir_b:
                result.append((dir_a, pkt_a, pkt_b))
    return result


def diff_packet(pkt_a: bytes, pkt_b: bytes) -> list[int]:
    """Return list of byte offsets that differ."""
    length = max(len(pkt_a), len(pkt_b))
    changed = []
    for i in range(length):
        ba = pkt_a[i] if i < len(pkt_a) else None
        bb = pkt_b[i] if i < len(pkt_b) else None
        if ba != bb:
            changed.append(i)
    return changed


def render_diff(pkt_a: bytes, pkt_b: bytes, changed: list[int]) -> str:
    """
    Render a side-by-side hex view with changed bytes highlighted.
    """
    def fmt_row(data: bytes, offset: int, highlight: list[int]) -> str:
        chunk = data[offset:offset+16]
        parts = []
        for i, b in enumerate(chunk):
            abs_off = offset + i
            cell = f"{b:02X}"
            if abs_off in highlight:
                cell = f"{YELLOW}{BOLD}{cell}{RESET}"
            else:
                cell = f"{DIM}{cell}{RESET}"
            parts.append(cell)
        return " ".join(parts)

    lines = []
    length = max(len(pkt_a), len(pkt_b))
    for offset in range(0, length, 16):
        off_str = f"{DIM}{offset:04X}{RESET}"
        row_a = fmt_row(pkt_a, offset, changed)
        row_b = fmt_row(pkt_b, offset, changed)
        lines.append(f"  {off_str}  A: {row_a}")
        lines.append(f"        B: {row_b}")
    return "\n".join(lines)


def summarize_changes(aligned: list[tuple]) -> dict[int, set]:
    """
    Map from byte offset → set of (value_in_a, value_in_b) that changed.
    A byte that consistently changes across all differing packets is likely
    a protocol field (command, color, mode, etc.).
    """
    offset_changes: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for _, pkt_a, pkt_b in aligned:
        for off in diff_packet(pkt_a, pkt_b):
            va = pkt_a[off] if off < len(pkt_a) else -1
            vb = pkt_b[off] if off < len(pkt_b) else -1
            offset_changes[off].append((va, vb))
    return offset_changes


def main():
    parser = argparse.ArgumentParser(
        description="Diff two sniffer logs to identify protocol byte positions"
    )
    parser.add_argument("log_a", help="First capture log (state A)")
    parser.add_argument("log_b", help="Second capture log (state B)")
    parser.add_argument("--direction", choices=["IN", "OUT"], default=None,
                        help="Only compare packets in this direction (OUT = host→device, IN = device→host)")
    parser.add_argument("--min-packets", type=int, default=1,
                        help="Only show byte offsets that change in at least N packets")
    parser.add_argument("--summary-only", action="store_true",
                        help="Only show the change summary, skip packet-by-packet diff")
    args = parser.parse_args()

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  lovingcool-control Protocol Diff{RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"  A: {args.log_a}")
    print(f"  B: {args.log_b}")
    if args.direction:
        print(f"  Direction filter: {args.direction}")
    print()

    pkts_a = parse_log(args.log_a, args.direction)
    pkts_b = parse_log(args.log_b, args.direction)

    print(f"Packets in A: {len(pkts_a)},  B: {len(pkts_b)}")

    if not pkts_a or not pkts_b:
        print(f"{RED}[ERROR]{RESET} One or both log files had no parseable packets.")
        sys.exit(1)

    aligned = align_packets(pkts_a, pkts_b)
    print(f"Aligned pairs: {len(aligned)}\n")

    changed_packets = [(d, a, b) for d, a, b in aligned if diff_packet(a, b)]
    print(f"Packets with differences: {len(changed_packets)}")

    if not args.summary_only:
        for idx, (direction, pkt_a, pkt_b) in enumerate(changed_packets[:10]):
            changed = diff_packet(pkt_a, pkt_b)
            print(f"\n{BOLD}─── Packet #{idx+1} ({direction})  {len(changed)} byte(s) changed ───{RESET}")
            print(render_diff(pkt_a, pkt_b, changed))
            if len(changed_packets) > 10 and idx == 9:
                print(f"\n{DIM}... ({len(changed_packets) - 10} more differing packets, use --summary-only){RESET}")

    # Summary table
    offset_changes = summarize_changes(aligned)
    frequent = {
        off: changes
        for off, changes in offset_changes.items()
        if len(changes) >= args.min_packets
    }

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  BYTE OFFSET SUMMARY (changed in ≥{args.min_packets} packet(s)){RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"  {'Offset':>8}  {'Count':>6}  {'Typical A → B values'}")
    print(f"  {'─'*8}  {'─'*6}  {'─'*40}")

    for off in sorted(frequent):
        changes = frequent[off]
        # Most common A→B pair
        pairs = {}
        for va, vb in changes:
            k = (va, vb)
            pairs[k] = pairs.get(k, 0) + 1
        top_pair = max(pairs, key=pairs.get)
        va, vb = top_pair
        print(f"  {CYAN}byte[{off:3d}]{RESET}  {len(changes):>6}  0x{va:02X} → 0x{vb:02X}   {DIM}(changes in {len(changes)} packets){RESET}")

    print()
    print(f"{YELLOW}Tip:{RESET} Offsets that change in EVERY packet are likely protocol fields.")
    print("Offsets that are always 0x00 in A but nonzero in B may be color/mode channels.")
    print("Run with --direction OUT to focus on host→device command packets.\n")


if __name__ == "__main__":
    main()
