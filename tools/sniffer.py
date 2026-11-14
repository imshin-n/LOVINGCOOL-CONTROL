#!/usr/bin/env python3
"""
lovingcool-control :: USB Packet Sniffer / Logger
==========================================
Records raw USB HID traffic to/from the AIO so we can reverse-engineer
the protocol for LCD and RGB commands.

WORKFLOW:
  1. Open Wireshark (or use usbmon on Linux) — OR just use this script's
     HID read-loop mode to capture device-initiated packets.
  2. Open the manufacturer's Windows software (in a VM or dual boot).
  3. Make one change at a time (e.g. set LCD to a solid red image).
  4. Save the packet capture and compare byte sequences.

Usage:
    # List interfaces available for capture
    python tools/sniffer.py --list

    # Read-loop: dump everything the device sends (device → host)
    python tools/sniffer.py --vid 0x0483 --pid 0x5750

    # Also send an initialization byte sequence and watch the response
    python tools/sniffer.py --vid 0x0483 --pid 0x5750 --init

    # Log to file
    python tools/sniffer.py --vid 0x0483 --pid 0x5750 --log capture.bin.log

Requirements:
    pip install hidapi
    On Linux: sudo python tools/sniffer.py (or install udev rules first)
"""

import argparse
import datetime
import sys
import time

try:
    import hid
except ImportError:
    print("[ERROR] Install hidapi: pip install hidapi")
    sys.exit(1)

try:
    import serial
    import serial.tools.list_ports as list_ports
    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"


def hexdump(data: bytes, prefix: str = "  ") -> str:
    """Classic hexdump with ASCII sidebar."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{prefix}{i:04X}  {hex_part:<47}  |{ascii_part}|")
    return "\n".join(lines)


def format_packet(data: bytes, direction: str, index: int) -> str:
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    arrow = f"{GREEN}←{RESET}" if direction == "IN" else f"{YELLOW}→{RESET}"
    header = f"{DIM}[{ts}]{RESET} {arrow} {BOLD}#{index:04d}{RESET}  {direction}  {len(data)} bytes"
    return header + "\n" + hexdump(data)


class PacketLogger:
    def __init__(self, log_path: str | None = None):
        self.log_path = log_path
        self._file = None
        self._index = 0
        if log_path:
            self._file = open(log_path, "w")
            print(f"{CYAN}Logging to {log_path}{RESET}")

    def log(self, data: bytes, direction: str):
        self._index += 1
        text = format_packet(data, direction, self._index)
        print(text)
        if self._file:
            self._file.write(text + "\n")
            self._file.flush()

    def close(self):
        if self._file:
            self._file.close()


class DeviceOpenError(Exception):
    pass


class SerialLink:
    """Adapter so a pyserial connection matches hid.device's write/read/close shape."""

    def __init__(self, ser: "serial.Serial"):
        self._ser = ser

    def write(self, data: bytes) -> int:
        n = self._ser.write(bytes(data))
        self._ser.flush()
        return n

    def read(self, size: int, timeout_ms: int = 100) -> list[int]:
        self._ser.timeout = timeout_ms / 1000.0
        return list(self._ser.read(size))

    def close(self):
        self._ser.close()


def _open_hid(vid: int, pid: int) -> hid.device:
    dev = hid.device()
    try:
        dev.open(vid, pid)
    except OSError as e:
        raise DeviceOpenError(str(e)) from e
    dev.set_nonblocking(True)
    mfr = dev.get_manufacturer_string()
    prod = dev.get_product_string()
    serial_str = dev.get_serial_number_string()
    print(f"\n{GREEN}[OPEN]{RESET}  VID:PID=0x{vid:04X}:0x{pid:04X}  [hid]")
    print(f"  Manufacturer : {mfr}")
    print(f"  Product      : {prod}")
    print(f"  Serial       : {serial_str}\n")
    return dev


def _open_serial(vid: int, pid: int, baudrate: int = 115200) -> SerialLink:
    if not HAS_PYSERIAL:
        raise DeviceOpenError("pyserial not installed: pip install pyserial")
    match = next((p for p in list_ports.comports() if p.vid == vid and p.pid == pid), None)
    if match is None:
        raise DeviceOpenError(f"no serial port found for VID:PID=0x{vid:04X}:0x{pid:04X}")
    ser = serial.Serial(match.device, baudrate=baudrate, timeout=0.1)
    print(f"\n{GREEN}[OPEN]{RESET}  VID:PID=0x{vid:04X}:0x{pid:04X}  [serial, {match.device} @ {baudrate}]")
    print(f"  Manufacturer : {match.manufacturer}")
    print(f"  Product      : {match.product or match.description}")
    print(f"  Serial       : {match.serial_number}\n")
    return SerialLink(ser)


def open_device(vid: int, pid: int, transport: str = "auto", baudrate: int = 115200):
    """
    Open the device via HID or USB-serial (CDC-ACM).

    transport="auto" tries HID first, then falls back to serial — many
    cheap AIO controllers (this one included) enumerate as a CDC-ACM
    virtual COM port, not HID, so the fallback matters.
    """
    errors = []
    if transport in ("auto", "hid"):
        try:
            return _open_hid(vid, pid)
        except DeviceOpenError as e:
            errors.append(f"hid: {e}")

    if transport in ("auto", "serial"):
        try:
            return _open_serial(vid, pid, baudrate)
        except DeviceOpenError as e:
            errors.append(f"serial: {e}")

    print(f"{RED}[ERROR]{RESET} Cannot open device 0x{vid:04X}:0x{pid:04X}")
    for e in errors:
        print(f"  - {e}")
    if any("ermission" in e or "ccess" in e for e in errors):
        print("\n  On Linux: sudo python tools/sniffer.py ...")
        print("  Or install udev rules: sudo cp packaging/linux/70-lovingcool-control.rules /etc/udev/rules.d/")
        print("  Then: sudo udevadm control --reload && sudo udevadm trigger")
    sys.exit(1)


def read_loop(dev, logger: PacketLogger, timeout_ms: int = 5000):
    """Continuously read from device and log all incoming packets."""
    print(f"{CYAN}Reading... (Ctrl+C to stop){RESET}\n")
    silence = 0
    try:
        while True:
            data = dev.read(64, timeout_ms=100)
            if data:
                silence = 0
                logger.log(bytes(data), "IN")
            else:
                silence += 1
                if silence % 50 == 0:
                    print(f"{DIM}  ... waiting for device data ...{RESET}")
    except KeyboardInterrupt:
        print(f"\n{CYAN}Stopped.{RESET}")


def send_probe(dev, logger: PacketLogger, payload: bytes):
    """Send a raw payload and capture the response."""
    if isinstance(dev, SerialLink):
        # CDC-ACM is a plain byte stream — no HID report framing to add.
        packet = payload
        logged = payload
    else:
        # HID write always prepends report ID (0x00 for no report ID)
        packet = bytes([0x00]) + payload
        packet = packet[:65]  # pad/truncate to 65 bytes (report ID + 64)
        packet += bytes(65 - len(packet))
        logged = packet[1:]

    print(f"\n{YELLOW}[SEND]{RESET}")
    logger.log(logged, "OUT")
    dev.write(packet)
    time.sleep(0.05)

    # Read response
    for _ in range(5):
        resp = dev.read(64, timeout_ms=200)
        if resp:
            logger.log(bytes(resp), "IN")
        else:
            break


# Common initialization sequences used by similar OEM AIO coolers (guesses).
# These are STARTING POINTS for reverse engineering — adjust based on captures.
PROBE_SEQUENCES = {
    "get_firmware": bytes([0x10, 0x00] + [0x00] * 62),
    "get_status":   bytes([0x20, 0x00] + [0x00] * 62),
    "lcd_init":     bytes([0x30, 0x01] + [0x00] * 62),
    "rgb_query":    bytes([0x40, 0x00] + [0x00] * 62),
}


def main():
    parser = argparse.ArgumentParser(description="Lovi.Cool AIO USB packet sniffer")
    parser.add_argument("--vid", type=lambda x: int(x, 16), help="Vendor ID (hex)")
    parser.add_argument("--pid", type=lambda x: int(x, 16), help="Product ID (hex)")
    parser.add_argument("--log", metavar="FILE", help="Write packets to log file")
    parser.add_argument("--init", action="store_true", help="Send probe sequences and capture responses")
    parser.add_argument("--send", metavar="HEX", help="Send a custom hex payload (e.g. '10 00 00 00')")
    parser.add_argument("--list", action="store_true", help="List available HID + USB-serial devices")
    parser.add_argument("--transport", choices=["auto", "hid", "serial"], default="auto",
                         help="Force transport (default: auto-detect HID then USB-serial)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate for serial transport")
    args = parser.parse_args()

    if args.list:
        print(f"\n{BOLD}Available HID devices:{RESET}\n")
        for d in hid.enumerate():
            vid = d["vendor_id"]
            pid = d["product_id"]
            prod = d.get("product_string") or "(no name)"
            print(f"  0x{vid:04X}:0x{pid:04X}  {prod}")
        print(f"\n{BOLD}Available USB-serial (CDC-ACM) devices:{RESET}\n")
        if not HAS_PYSERIAL:
            print(f"  {YELLOW}pyserial not installed — pip install pyserial{RESET}")
        else:
            found = False
            for p in list_ports.comports():
                if p.vid is None:
                    continue
                found = True
                prod = p.product or p.description or "(no name)"
                print(f"  0x{p.vid:04X}:0x{p.pid:04X}  {prod}  ({p.device})")
            if not found:
                print("  (none)")
        return

    if not args.vid or not args.pid:
        print(f"{RED}[ERROR]{RESET} --vid and --pid are required (use --list to find them)")
        parser.print_help()
        sys.exit(1)

    dev = open_device(args.vid, args.pid, transport=args.transport, baudrate=args.baud)
    logger = PacketLogger(args.log)

    try:
        if args.send:
            raw = bytes(int(x, 16) for x in args.send.split())
            send_probe(dev, logger, raw)
        elif args.init:
            print(f"{CYAN}Sending probe sequences...{RESET}\n")
            for name, seq in PROBE_SEQUENCES.items():
                print(f"{BOLD}>>> {name}{RESET}")
                send_probe(dev, logger, seq)
                time.sleep(0.2)
        else:
            read_loop(dev, logger)
    finally:
        logger.close()
        dev.close()


if __name__ == "__main__":
    main()
