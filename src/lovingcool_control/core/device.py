"""
lovingcool-control :: core/device.py
====================================
Which coolers we know (devices.toml), finding them on USB, and the
connection object everything else talks through.

The wire format lives in core/protocol.py. This module only moves bytes.
One AIODevice is shared by every user of a cooler, and all of its I/O runs
under one re-entrant lock: a write, or a command and its reply, can never
be split by another thread's writes. exclusive() extends that over several
operations (e.g. "commit, then send a frame").
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from importlib import resources
from pathlib import Path

import serial
import serial.tools.list_ports as list_ports

try:
    import hid  # only for USB-HID screens; every known model is a serial (CDC-ACM) device
except ImportError:
    hid = None

from . import protocol

log = logging.getLogger(__name__)

APP_NAME = "lovingcool-control"
VENDOR_ID = 0x33C3   # the screen controllers' USB vendor ID


class DeviceError(Exception):
    """Opening or talking to a device failed."""


# ── Known devices ────────────────────────────────────────────

@dataclass
class DeviceSpec:
    name: str
    vid: int
    pid: int
    transport: str = "hid"                 # "serial" (CDC-ACM) or "hid"
    baudrate: int = 115200                 # serial
    dtr: bool = False                      # serial: raise DTR and use DSR/DTR flow control
    lcd_width: int = 240                   # used until the device reports its own size
    lcd_height: int = 240
    lcd_color_format: str = "JPEG"         # the only format implemented (JPEG frames)
    packet_size: int = 64                  # HID report size
    report_id: int = 0x00                  # HID
    interface: int = 0                     # HID
    cmd_restart: int = 0x01                # command-channel keys, see core/protocol.py
    cmd_set_brightness: int = 0x03
    cmd_get_info: int = 0x06
    cmd_commit: int = 0x11
    cmd_close: int = 0x21
    keepalive_ms: int = 0                  # re-send the last frame this often if the panel blanks
    tags: list[str] = field(default_factory=list)
    verified: bool = False

    @property
    def usb_id(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}"


def user_config_dir() -> Path:
    """Per-user settings folder for this app on each platform."""
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = str(Path.home() / "Library" / "Application Support")
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / APP_NAME


_FIELDS = frozenset(f.name for f in fields(DeviceSpec))


def parse_device_specs(text: str, source: str = "<string>") -> list[DeviceSpec]:
    """Parse devices.toml text. Misspelt fields are errors rather than silently ignored."""
    specs = []
    for n, entry in enumerate(tomllib.loads(text).get("device", []), start=1):
        extra = sorted(set(entry) - _FIELDS)
        if extra:
            raise ValueError(f"{source}: device #{n}: unknown field(s) {extra}")
        try:
            specs.append(DeviceSpec(**entry))
        except TypeError as e:   # e.g. a required field missing
            raise ValueError(f"{source}: device #{n}: {e}") from e
    return specs


def load_device_specs(user_file: Path | None = None) -> list[DeviceSpec]:
    """The built-in list, with the user's devices.toml layered on top (same USB ID wins)."""
    builtin = resources.files(__package__).joinpath("devices.toml").read_text("utf8")
    by_id = {(s.vid, s.pid): s for s in parse_device_specs(builtin, "devices.toml")}
    path = user_file or user_config_dir() / "devices.toml"
    if path.is_file():
        try:
            by_id.update({(s.vid, s.pid): s for s in parse_device_specs(path.read_text("utf8"), str(path))})
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            log.error("Ignoring %s: %s", path, e)
    return list(by_id.values())


KNOWN_DEVICES: list[DeviceSpec] = load_device_specs()


# ── Connection ───────────────────────────────────────────────

class AIODevice:
    """An open (or openable) connection to one cooler's screen controller."""

    def __init__(self, spec: DeviceSpec):
        self.spec = spec
        self.info: dict = {}                    # last get_info() reply
        self._dev = None                        # serial.Serial, or hid.device
        self._connected = False
        self._lock = threading.RLock()
        self._on_disconnect: Callable[[], None] | None = None

    @property
    def is_serial(self) -> bool:
        return self.spec.transport == "serial"

    @property
    def connected(self) -> bool:
        return self._connected

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Called when an I/O error drops the connection."""
        self._on_disconnect = callback

    @contextmanager
    def exclusive(self) -> Iterator[AIODevice]:
        with self._lock:
            yield self

    # Opening and closing

    def connect(self) -> None:
        with self._lock:
            self._dev = self._open_serial() if self.is_serial else self._open_hid()
            self._connected = True

    def _open_serial(self):
        port = find_serial_port(self.spec.vid, self.spec.pid)
        if port is None:
            raise DeviceError(f"{self.spec.usb_id} has no serial port (unplugged, or held by another program?)")
        try:
            # exclusive=True: a second program gets "busy" instead of mixing its frames into ours.
            link = serial.Serial(port, baudrate=self.spec.baudrate, timeout=0.1,
                                 dsrdtr=self.spec.dtr, exclusive=True)
        except (OSError, serial.SerialException) as e:
            raise DeviceError(f"Can't open {port}: {e}") from e
        if self.spec.dtr:
            link.dtr = True
            time.sleep(0.2)            # the controller's USB stack needs a moment after DTR rises
            link.reset_input_buffer()
        log.info("Connected to %s (%s) on %s", self.spec.name, self.spec.usb_id, port)
        return link

    def _open_hid(self):
        if hid is None:
            raise DeviceError("This device is USB HID: install hidapi (pip install hidapi / python-hidapi)")
        handle = hid.device()
        try:
            handle.open(self.spec.vid, self.spec.pid)
        except OSError as e:
            raise DeviceError(f"Can't open HID device {self.spec.usb_id}: {e}") from e
        handle.set_nonblocking(True)
        log.info("Connected to %s (%s) over HID", self.spec.name, self.spec.usb_id)
        return handle

    def disconnect(self) -> None:
        with self._lock:
            link, self._dev, self._connected = self._dev, None, False
            if link is not None:
                try:
                    link.close()
                except Exception:
                    pass
        log.info("Disconnected %s", self.spec.name)

    # Raw I/O (callers hold the lock)

    def _fail(self, action: str, error: Exception) -> DeviceError:
        self._connected = False
        if self._on_disconnect:
            self._on_disconnect()
        return DeviceError(f"{action} failed: {error}")

    def _require_open(self) -> None:
        if self._dev is None or not self._connected:
            raise DeviceError("Device not connected")

    def _write_raw(self, data: bytes) -> None:
        try:
            self._dev.write(data)
            if self.is_serial:
                self._dev.flush()
        except (OSError, serial.SerialException) as e:
            raise self._fail("Write", e) from e

    def _read_raw(self, size: int, timeout_s: float | None = None) -> bytes:
        try:
            if not self.is_serial:
                return bytes(self._dev.read(size, timeout_ms=int((timeout_s or 0.1) * 1000)))
            # Changing pyserial's timeout reconfigures the port, which disturbs a 2 Mbaud
            # link, so only touch it when it actually changes.
            if timeout_s is not None and self._dev.timeout != timeout_s:
                self._dev.timeout = timeout_s
            return bytes(self._dev.read(size))
        except (OSError, serial.SerialException) as e:
            raise self._fail("Read", e) from e

    # Packets

    def send_packet(self, payload: bytes) -> None:
        """Write one payload: as-is on serial; as a padded, report-ID-prefixed report on HID."""
        with self._lock:
            self._require_open()
            if self.is_serial:
                data = payload
            else:
                report = self.spec.packet_size + 1
                data = (bytes([self.spec.report_id]) + payload)[:report].ljust(report, b"\x00")
            if log.isEnabledFor(logging.DEBUG):
                log.debug("TX %d B: %s", len(data), data[:64].hex(" "))
            self._write_raw(data)

    def read_packet(self, timeout_ms: int = 100) -> bytes | None:
        """Whatever arrives within timeout_ms (for tools), or None."""
        with self._lock:
            if self._dev is None or not self._connected:
                return None
            data = self._read_raw(self.spec.packet_size, timeout_ms / 1000.0)
        if data:
            log.debug("RX %d B: %s", len(data), data.hex(" "))
        return data or None

    # Command channel (serial screens)

    def _await_reply(self, timeout_ms: int) -> protocol.Frame | None:
        """
        Wait for one valid reply frame. On real hardware, reading header and
        body as two exact-size reads straight after the write lost bytes;
        pausing briefly and then reading in bulk is reliable, and the streaming
        parser copes with replies split across reads or preceded by noise.
        """
        parser = protocol.FrameParser()
        give_up = time.monotonic() + timeout_ms / 1000.0
        time.sleep(0.15)
        while True:
            frames = parser.feed(self._read_raw(4096))
            if frames:
                return frames[0]
            if time.monotonic() >= give_up:
                if parser.dropped:
                    log.warning("Discarded %d corrupt reply frame(s)", parser.dropped)
                return None

    def send_command(self, key: int, payload: bytes = b"", expect_response: bool = True,
                     timeout_ms: int = 3000) -> dict | None:
        """Send a command frame; with expect_response, return its JSON reply."""
        if not self.is_serial:
            raise DeviceError("The command channel is only implemented for serial devices")
        frame = protocol.build_frame(key, payload)
        with self._lock:
            if expect_response and self._dev is not None:
                try:
                    self._dev.reset_input_buffer()   # forget leftovers from earlier traffic
                except (OSError, serial.SerialException) as e:
                    raise self._fail("Flush", e) from e
            self.send_packet(frame)
            reply = self._await_reply(timeout_ms) if expect_response else None
        if expect_response and reply is None:
            raise DeviceError(f"No valid reply to command 0x{key:02X}")
        return reply.json() if reply else None

    def get_info(self) -> dict:
        """Ask the screen about itself (size, model, firmware, brightness...). Also kept in .info."""
        data = (self.send_command(self.spec.cmd_get_info) or {}).get("data")
        self.info = data if isinstance(data, dict) else {}
        return self.info

    @property
    def panel_size(self) -> tuple[int, int]:
        """Native resolution: as the device reported it, else from the spec."""
        w, h = self.info.get("width"), self.info.get("height")
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            return w, h
        return self.spec.lcd_width, self.spec.lcd_height

    def set_brightness(self, percent: int) -> None:
        level = min(100, max(0, int(percent)))
        self.send_command(self.spec.cmd_set_brightness, bytes([level]), expect_response=False)

    def restart(self) -> None:
        self.send_command(self.spec.cmd_restart, expect_response=False)

    def __repr__(self) -> str:
        return f"<AIODevice {self.spec.name} {self.spec.usb_id} {'open' if self._connected else 'closed'}>"


# ── Finding devices ──────────────────────────────────────────

def find_serial_port(vid: int, pid: int) -> str | None:
    """The port path (/dev/ttyACM0, COM3, ...) of a USB serial device, if present."""
    return next((p.device for p in list_ports.comports() if (p.vid, p.pid) == (vid, pid)), None)


def _present_ids() -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    serial_ids = {(p.vid, p.pid) for p in list_ports.comports() if p.vid is not None}
    hid_ids = {(d["vendor_id"], d["product_id"]) for d in hid.enumerate()} if hid else set()
    return serial_ids, hid_ids


def find_devices(extra_specs: list[DeviceSpec] | None = None) -> list[AIODevice]:
    """A (not yet connected) AIODevice for each known cooler that is plugged in."""
    serial_ids, hid_ids = _present_ids()
    found = []
    for spec in [*KNOWN_DEVICES, *(extra_specs or [])]:
        present = serial_ids if spec.transport == "serial" else hid_ids
        if (spec.vid, spec.pid) in present:
            log.info("Found %s (%s)", spec.name, spec.transport)
            found.append(AIODevice(spec))
    return found


@dataclass
class UsbCandidate:
    """A USB device that might be an unrecognised cooler controller."""
    vendor_id: int
    product_id: int
    product: str
    manufacturer: str
    transport: str          # "serial" or "hid"
    path: str
    score: int


_HINT_WORDS = ("lcd", "screen", "display", "monitor", "cool", "aio", "pump", "lovi", "rt-thread", "rtt")


def _likelihood(vid: int, product: str, manufacturer: str, vendor_page: bool, transport: str) -> int:
    text = f"{product} {manufacturer}".lower()
    score = 5 * sum(word in text for word in _HINT_WORDS)
    score += 20 if vid == VENDOR_ID else 0
    score += 3 if vendor_page else 0                 # HID vendor-defined usage page
    score += 2 if transport == "serial" else 0       # the known screens are all CDC-ACM serial
    return score


def _hid_path(raw) -> str:
    return raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw or "")


def find_any_cooler_candidates() -> list[UsbCandidate]:
    """Plugged-in USB serial and HID devices, likeliest cooler first (for the Information tab)."""
    out = []
    for p in list_ports.comports():
        if p.vid is None:
            continue   # built-in UARTs
        product, maker = p.product or p.description or "", p.manufacturer or ""
        out.append(UsbCandidate(p.vid, p.pid, product, maker, "serial", p.device,
                                _likelihood(p.vid, product, maker, False, "serial")))
    for d in (hid.enumerate() if hid else []):
        product, maker = d.get("product_string") or "", d.get("manufacturer_string") or ""
        score = _likelihood(d["vendor_id"], product, maker, d.get("usage_page", 0) >= 0xFF00, "hid")
        if score:
            out.append(UsbCandidate(d["vendor_id"], d["product_id"], product, maker, "hid",
                                    _hid_path(d.get("path")), score))
    return sorted(out, key=lambda c: c.score, reverse=True)
