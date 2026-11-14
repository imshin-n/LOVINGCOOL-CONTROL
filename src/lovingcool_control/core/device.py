"""
lovingcool-control :: core/device.py
====================================
Device registry, discovery and the connection object.

The device list lives in devices.toml next to this file, plus an
optional per-user override file (see load_device_specs()). The wire
format is in core/protocol.py; this module does the I/O.

Thread safety: one AIODevice is shared by everything that talks to the
cooler (LCD playback threads, the GUI, the protocol lab). Every write,
and every command's write+reply exchange, holds the device lock, so a
background frame stream can never interleave bytes with a command or
with another stream.
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

try:
    import hid
except ImportError as e:
    raise ImportError("Install hidapi: pip install hidapi") from e

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError as e:
    raise ImportError("Install pyserial: pip install pyserial") from e

from . import protocol

log = logging.getLogger(__name__)

APP_NAME = "lovingcool-control"


# ──────────────────────────────────────────────────────────────
#  Device registry
# ──────────────────────────────────────────────────────────────

@dataclass
class DeviceSpec:
    name: str
    vid: int
    pid: int
    # "hid" for USB HID (hidapi), "serial" for USB CDC-ACM virtual COM ports.
    # The 33C3 LCD controllers are serial; check with tools/enumerate.py.
    transport: str = "hid"
    baudrate: int = 115200   # serial only
    dtr: bool = False        # assert DTR and enable DSR/DTR flow control on open
    # Fallback panel size; AIODevice.panel_size prefers the device's own reply.
    lcd_width: int = 240
    lcd_height: int = 240
    lcd_color_format: str = "RGB565"   # or "RGB888", "BGR565", "JPEG"
    # HID only
    packet_size: int = 64
    report_id: int = 0x00
    interface: int = 0
    # Command-channel opcodes (33C3:7788 family, from the vendor app source)
    cmd_restart: int = 0x01
    cmd_set_brightness: int = 0x03
    cmd_get_info: int = 0x06
    cmd_commit: int = 0x11        # "start live", sent once before streaming frames
    cmd_close: int = 0x21
    tags: list[str] = field(default_factory=list)
    verified: bool = False

    @property
    def usb_id(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}"


_SPEC_FIELDS = {f.name for f in fields(DeviceSpec)}


def user_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def parse_device_specs(text: str, source: str = "<string>") -> list[DeviceSpec]:
    """Parse a devices.toml document into DeviceSpecs. Unknown keys are an error, so typos surface."""
    data = tomllib.loads(text)
    specs = []
    for i, entry in enumerate(data.get("device", [])):
        unknown = set(entry) - _SPEC_FIELDS
        if unknown:
            raise ValueError(f"{source}: device #{i + 1}: unknown field(s) {sorted(unknown)}")
        try:
            specs.append(DeviceSpec(**entry))
        except TypeError as e:
            raise ValueError(f"{source}: device #{i + 1}: {e}") from e
    return specs


def load_device_specs(user_file: Path | None = None) -> list[DeviceSpec]:
    """Built-in devices.toml, then the user's file; user entries replace built-ins with the same vid/pid."""
    builtin = resources.files(__package__).joinpath("devices.toml").read_text("utf8")
    merged = {(s.vid, s.pid): s for s in parse_device_specs(builtin, "devices.toml")}
    user_file = user_file or user_config_dir() / "devices.toml"
    if user_file.is_file():
        try:
            for s in parse_device_specs(user_file.read_text("utf8"), str(user_file)):
                merged[(s.vid, s.pid)] = s
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            log.error("Ignoring %s: %s", user_file, e)
    return list(merged.values())


KNOWN_DEVICES: list[DeviceSpec] = load_device_specs()


# ──────────────────────────────────────────────────────────────
#  Connection
# ──────────────────────────────────────────────────────────────

class DeviceNotFoundError(Exception):
    pass


class DeviceError(Exception):
    pass


class AIODevice:
    """A connection to one cooler controller, over serial (CDC-ACM) or HID."""

    def __init__(self, spec: DeviceSpec):
        self.spec = spec
        self._dev: hid.device | serial.Serial | None = None
        self._connected = False
        self._on_disconnect: Callable[[], None] | None = None
        self._lock = threading.RLock()
        self.info: dict = {}   # last get_info() reply

    # ── Connection lifecycle ──────────────────────────────────

    def connect(self) -> None:
        with self._lock:
            if self.spec.transport == "serial":
                self._connect_serial()
            else:
                self._connect_hid()

    def _connect_hid(self) -> None:
        self._dev = hid.device()
        try:
            self._dev.open(self.spec.vid, self.spec.pid)
            self._dev.set_nonblocking(True)
            self._connected = True
            log.info("Connected to %s (%s)", self.spec.name, self.spec.usb_id)
        except OSError as e:
            self._connected = False
            raise DeviceError(f"Cannot open device: {e}") from e

    def _connect_serial(self) -> None:
        port = find_serial_port(self.spec.vid, self.spec.pid)
        if port is None:
            self._connected = False
            raise DeviceError(
                f"No serial port found for {self.spec.usb_id} "
                f"(device unplugged, or grabbed by another process; check ModemManager)"
            )
        try:
            # dsrdtr: at 2 Mbaud the 7788 drops/corrupts bytes without DTR flow control.
            self._dev = serial.Serial(port, baudrate=self.spec.baudrate, timeout=0.1,
                                      dsrdtr=self.spec.dtr)
            if self.spec.dtr:
                self._dev.dtr = True
                time.sleep(0.2)   # let the MCU's USB-CDC stack settle after DTR
                self._dev.reset_input_buffer()
            self._connected = True
            log.info("Connected to %s (%s) at %s", self.spec.name, self.spec.usb_id, port)
        except (OSError, serial.SerialException) as e:
            self._connected = False
            raise DeviceError(f"Cannot open serial port {port}: {e}") from e

    def disconnect(self) -> None:
        with self._lock:
            if self._dev:
                try:
                    self._dev.close()
                except Exception:
                    pass
                self._dev = None
            self._connected = False
        log.info("Disconnected from %s", self.spec.name)

    @property
    def connected(self) -> bool:
        return self._connected

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register a callback for when an I/O error drops the connection."""
        self._on_disconnect = callback

    @contextmanager
    def exclusive(self) -> Iterator[AIODevice]:
        """Hold the device lock across several operations (e.g. a multi-part frame)."""
        with self._lock:
            yield self

    def _io_failed(self, what: str, e: Exception) -> DeviceError:
        self._connected = False
        if self._on_disconnect:
            self._on_disconnect()
        return DeviceError(f"{what} failed: {e}")

    # ── Raw packet I/O ───────────────────────────────────────

    def send_packet(self, payload: bytes) -> None:
        """
        Write one payload. Serial: as-is (CDC-ACM is a byte stream).
        HID: report ID prepended, padded/truncated to packet_size.
        """
        with self._lock:
            if not self._connected or not self._dev:
                raise DeviceError("Device not connected")
            if self.spec.transport == "serial":
                packet = payload
            else:
                size = self.spec.packet_size
                packet = (bytes([self.spec.report_id]) + payload).ljust(size + 1, b"\0")[: size + 1]
            if log.isEnabledFor(logging.DEBUG):
                log.debug("TX %d bytes: %s", len(packet), packet[:64].hex(" "))
            try:
                self._dev.write(packet)
                if self.spec.transport == "serial":
                    self._dev.flush()
            except (OSError, serial.SerialException) as e:
                raise self._io_failed("Write", e) from e

    def read_packet(self, timeout_ms: int = 100) -> bytes | None:
        """Read whatever arrives within timeout_ms (protocol-lab use). None on timeout."""
        with self._lock:
            if not self._connected or not self._dev:
                return None
            try:
                if self.spec.transport == "serial":
                    # Only reassign .timeout when it changes: pyserial calls
                    # tcsetattr on every set, which disturbs reads at 2 Mbaud.
                    new_timeout = timeout_ms / 1000.0
                    if self._dev.timeout != new_timeout:
                        self._dev.timeout = new_timeout
                    data = self._dev.read(self.spec.packet_size)
                else:
                    data = self._dev.read(self.spec.packet_size, timeout_ms=timeout_ms)
            except (OSError, serial.SerialException) as e:
                raise self._io_failed("Read", e) from e
        if data:
            log.debug("RX: %s", bytes(data).hex(" "))
            return bytes(data)
        return None

    # ── Command channel (serial, 33C3 family) ────────────────

    def _read_reply(self, timeout_ms: int) -> protocol.Frame | None:
        """
        Collect bytes until one complete, checksum-valid frame arrives or the
        deadline passes. Caller holds the lock.

        Found on real hardware: reading the header and then the body as two
        exact-size reads straight after a write returned truncated frames.
        A short pause followed by bulk reads of whatever has arrived is
        reliable, so that's what this does, feeding everything through the
        streaming parser instead of trusting a single read to hold the frame.
        """
        parser = protocol.FrameParser()
        deadline = time.monotonic() + timeout_ms / 1000.0
        time.sleep(0.15)
        while True:
            try:
                chunk = self._dev.read(4096)
            except (OSError, serial.SerialException) as e:
                raise self._io_failed("Read", e) from e
            if chunk:
                frames = parser.feed(chunk)
                if frames:
                    return frames[0]
            if time.monotonic() >= deadline:
                if parser.dropped:
                    log.warning("Dropped %d corrupt reply frame(s)", parser.dropped)
                return None

    def send_command(self, key: int, payload: bytes = b"",
                     expect_response: bool = True, timeout_ms: int = 3000) -> dict | None:
        """Send a command frame and, if expected, return its JSON reply."""
        if self.spec.transport != "serial":
            raise DeviceError("The command channel is only implemented for serial devices")
        frame = protocol.build_frame(key, payload)
        with self._lock:
            if expect_response and self._dev is not None:
                try:
                    self._dev.reset_input_buffer()   # drop stale bytes from earlier traffic
                except (OSError, serial.SerialException) as e:
                    raise self._io_failed("Flush", e) from e
            self.send_packet(frame)
            if not expect_response:
                return None
            reply = self._read_reply(timeout_ms)
        if reply is None:
            raise DeviceError(f"No valid response to command 0x{key:02X}")
        return reply.json()

    def get_info(self) -> dict:
        """Query width, height, brightness, model, version, uid, region. Cached in .info."""
        resp = self.send_command(self.spec.cmd_get_info) or {}
        self.info = resp.get("data", {}) if isinstance(resp.get("data"), dict) else {}
        return self.info

    @property
    def panel_size(self) -> tuple[int, int]:
        """Native panel raster: the device's own get_info reply if we have one, else the spec."""
        w, h = self.info.get("width"), self.info.get("height")
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            return w, h
        return self.spec.lcd_width, self.spec.lcd_height

    def set_brightness(self, percent: int) -> None:
        """Set LCD backlight brightness, 0-100."""
        percent = max(0, min(100, percent))
        self.send_command(self.spec.cmd_set_brightness, bytes([percent]), expect_response=False)

    def restart(self) -> None:
        """Restart the device's LCD controller."""
        self.send_command(self.spec.cmd_restart, expect_response=False)

    def __repr__(self) -> str:
        state = "connected" if self._connected else "disconnected"
        return f"<AIODevice {self.spec.name} {self.spec.usb_id} [{state}]>"


# ──────────────────────────────────────────────────────────────
#  Discovery
# ──────────────────────────────────────────────────────────────

def find_serial_port(vid: int, pid: int) -> str | None:
    """Find the serial port path (e.g. /dev/ttyACM0, COM3) for a VID:PID."""
    for p in list_ports.comports():
        if p.vid == vid and p.pid == pid:
            return p.device
    return None


def find_devices(extra_specs: list[DeviceSpec] | None = None) -> list[AIODevice]:
    """Return an AIODevice for every connected USB HID or serial device matching a known spec."""
    specs = KNOWN_DEVICES + (extra_specs or [])
    connected_hids = {(d["vendor_id"], d["product_id"]) for d in hid.enumerate()}
    connected_serial = {(p.vid, p.pid) for p in list_ports.comports() if p.vid is not None}
    found = []
    for spec in specs:
        key = (spec.vid, spec.pid)
        if spec.transport == "serial" and key in connected_serial:
            found.append(AIODevice(spec))
            log.info("Found: %s (serial)", spec.name)
        elif spec.transport != "serial" and key in connected_hids:
            found.append(AIODevice(spec))
            log.info("Found: %s (hid)", spec.name)
    return found


def find_any_cooler_candidates() -> list[dict]:
    """
    Raw HID + serial enumeration entries that look like they could be an
    AIO controller, best match first. Used by the GUI's discovery list.
    """
    candidates = []
    keywords = ["cooler", "aio", "pump", "lcd", "cool", "lovi", "rt-thread", "rtt"]

    for d in hid.enumerate():
        prod = (d.get("product_string") or "").lower()
        mfr = (d.get("manufacturer_string") or "").lower()
        score = sum(10 for kw in keywords if kw in prod or kw in mfr)
        if d.get("usage_page", 0) >= 0xFF00:
            score += 5
        if score > 0:
            candidates.append({**d, "_transport": "hid", "_score": score})

    for p in list_ports.comports():
        if p.vid is None:
            continue
        prod = (p.product or p.description or "").lower()
        mfr = (p.manufacturer or "").lower()
        # Any USB-serial device with a real VID/PID is already a decent candidate.
        score = 3 + sum(10 for kw in keywords if kw in prod or kw in mfr)
        candidates.append({
            "vendor_id": p.vid,
            "product_id": p.pid,
            "product_string": p.product or p.description,
            "manufacturer_string": p.manufacturer,
            "usage_page": 0,
            "path": p.device,
            "_transport": "serial",
            "_score": score,
        })

    return sorted(candidates, key=lambda x: x["_score"], reverse=True)
