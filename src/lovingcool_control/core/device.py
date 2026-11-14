"""
lovingcool-control :: core/device.py
============================
Device discovery and connection management.

IMPORTANT: The VID/PID and protocol constants below are PLACEHOLDERS.
Run tools/enumerate.py to find your device's actual VID:PID, then update
KNOWN_DEVICES accordingly. The protocol fields (LCD_CMD, RGB_CMD etc.)
must be reverse-engineered using tools/sniffer.py.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

try:
    import hid
except ImportError as e:
    raise ImportError("Install hidapi: pip install hidapi") from e

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError as e:
    raise ImportError("Install pyserial: pip install pyserial") from e

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  Device Registry
#  Add your VID:PID here after running tools/enumerate.py
# ──────────────────────────────────────────────────────────────

@dataclass
class DeviceSpec:
    name: str
    vid: int
    pid: int
    # Transport: "hid" for USB HID devices (hidapi), "serial" for USB
    # CDC-ACM virtual COM port devices (many cheap MCU-based AIO/RGB
    # controllers — e.g. RT-Thread based boards — enumerate this way
    # instead of as HID; check both with tools/enumerate.py).
    transport: str = "hid"
    baudrate: int = 115200   # only used when transport == "serial"
    dtr: bool = False        # some MCU firmwares require DTR asserted to talk
    # LCD properties. lcd_color_format="JPEG" means the device expects
    # JPEG-encoded frames (see core/lcd.py) rather than raw pixel data.
    lcd_width: int = 240
    lcd_height: int = 240
    lcd_color_format: str = "RGB565"   # or "RGB888", "BGR565", "JPEG"
    # USB packet properties (HID transport only)
    packet_size: int = 64
    report_id: int = 0x00
    # Command-channel opcodes for the [0x55, 0xAA, len, key, payload, checksum]
    # framed protocol (see AIODevice.send_command). CONFIRMED against real
    # hardware for the 33C3:7788 family (Dongguan Hongtai "LOVINGCOOL
    # MONITOR" firmware) by extracting and testing the vendor's own
    # Electron app source (main/_baseClass/device.js) — not a guess.
    cmd_restart: int = 0x01
    cmd_set_brightness: int = 0x03
    cmd_get_info: int = 0x06
    cmd_commit: int = 0x11        # "start live" / flush, sent before streaming frames
    cmd_close: int = 0x21
    # Optional: interface number if device has multiple HID interfaces
    interface: int = 0
    # Extra tags for display
    tags: list[str] = field(default_factory=list)


KNOWN_DEVICES: list[DeviceSpec] = [
    # Found via tools/enumerate.py --serial: this unit's controller board
    # exposes a USB CDC-ACM virtual COM port (RT-Thread RTOS USB stack),
    # NOT a HID device — that's why plain hidapi enumeration never saw it.
    #
    # Protocol confirmed live against real hardware (see README) —
    # getDeviceInfo (cmd_get_info) returned:
    #   {"width":480,"height":320,"brightness":80,"model":"txm818-7inch",
    #    "version":"Ver1.0","uid":"0C014F018787","region":"00000"}
    DeviceSpec(
        name="Lovi.Cool AIO (Dongguan Hongtai txm818-7inch)",
        vid=0x33c3,
        pid=0x7788,
        transport="serial",
        baudrate=2_000_000,   # confirmed: SerialPort({ baudRate: 0x1e8480, dtr: true })
        dtr=True,
        lcd_width=480,
        lcd_height=320,
        lcd_color_format="JPEG",
        tags=["lcd"],
    ),
    # Lovingcool SC60 4.0-inch (VID 0x33C3, PID 0x7791)
    DeviceSpec(
        name="Lovingcool SC60 4.0-inch",
        vid=0x33c3,
        pid=0x7791,
        transport="serial",
        baudrate=2_000_000,
        dtr=False,
        lcd_width=480,
        lcd_height=480,
        lcd_color_format="JPEG",
        tags=["lcd"],
    ),
    # This vendor's app also whitelists PIDs 7797 / 7791 for sibling brand
    # builds (DELTA_CENTRO etc.) on the same VID — likely same protocol,
    # unconfirmed on this hardware:
    # DeviceSpec(name="Lovi.Cool AIO (variant)", vid=0x33c3, pid=0x7797, transport="serial", baudrate=2_000_000, dtr=True, lcd_color_format="JPEG"),
    #
    # RGB/fan lighting on this product line is a SEPARATE USB device (not
    # the LCD controller above) — the vendor app looks for it on a
    # different VID/PID whitelist entirely (commonly CH340-family USB-serial
    # bridges: VID 0x1A86, or 0xA012 / 0xAA12). It did not enumerate on this
    # machine, so it's either not wired to this unit's USB header or not
    # connected. Run tools/enumerate.py --watch and plug in any second USB
    # cable from the AIO to find it. See core/rgb.py.
]


# ──────────────────────────────────────────────────────────────
#  Device Connection
# ──────────────────────────────────────────────────────────────

class DeviceNotFoundError(Exception):
    pass

class DeviceError(Exception):
    pass


class AIODevice:
    """
    Wraps an HID connection to the cooler.

    All raw USB writes go through send_packet(). Once the protocol is
    known, higher-level methods (set_lcd_frame, set_rgb) will be built
    on top of this.
    """

    def __init__(self, spec: DeviceSpec):
        self.spec = spec
        self._dev: hid.device | serial.Serial | None = None
        self._connected = False
        self._on_disconnect: Callable | None = None

    # ── Connection lifecycle ──────────────────────────────────

    def connect(self) -> None:
        """Open the connection, using HID or serial depending on spec.transport."""
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
            log.info("Connected to %s (VID:PID=0x%04X:0x%04X)",
                     self.spec.name, self.spec.vid, self.spec.pid)
        except OSError as e:
            self._connected = False
            raise DeviceError(f"Cannot open device: {e}") from e

    def _connect_serial(self) -> None:
        port = find_serial_port(self.spec.vid, self.spec.pid)
        if port is None:
            self._connected = False
            raise DeviceError(
                f"No serial port found for VID:PID=0x{self.spec.vid:04X}:0x{self.spec.pid:04X} "
                f"(device unplugged, or grabbed by another process — check ModemManager)"
            )
        try:
            # dsrdtr=True: at 2Mbaud this device drops/corrupts bytes without
            # DTR-based flow control enabled — confirmed by testing both ways
            # against real hardware.
            self._dev = serial.Serial(port, baudrate=self.spec.baudrate, timeout=0.1,
                                       dsrdtr=self.spec.dtr)
            if self.spec.dtr:
                self._dev.dtr = True
                time.sleep(0.2)   # let the MCU's USB-CDC stack settle after DTR
                self._dev.reset_input_buffer()
            self._connected = True
            log.info("Connected to %s (VID:PID=0x%04X:0x%04X) at %s",
                      self.spec.name, self.spec.vid, self.spec.pid, port)
        except (OSError, serial.SerialException) as e:
            self._connected = False
            raise DeviceError(f"Cannot open serial port {port}: {e}") from e

    def disconnect(self) -> None:
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

    def on_disconnect(self, callback: Callable) -> None:
        """Register a callback for when the device is disconnected."""
        self._on_disconnect = callback

    # ── Raw packet I/O ───────────────────────────────────────

    def send_packet(self, payload: bytes) -> None:
        """
        Send a raw payload to the device.

        HID: prepends report ID and pads/truncates to packet_size (fixed-size
        reports are a HID requirement).
        Serial: writes the payload as-is — CDC-ACM is a plain byte stream,
        there's no report framing to add.
        """
        if not self._connected or not self._dev:
            raise DeviceError("Device not connected")

        if self.spec.transport == "serial":
            packet = payload
        else:
            size = self.spec.packet_size
            packet = bytes([self.spec.report_id]) + payload
            if len(packet) < size + 1:
                packet += bytes(size + 1 - len(packet))
            else:
                packet = packet[:size + 1]

        log.debug("TX: %s", packet.hex(" "))
        try:
            self._dev.write(packet)
            if self.spec.transport == "serial":
                self._dev.flush()
        except (OSError, serial.SerialException) as e:
            self._connected = False
            if self._on_disconnect:
                self._on_disconnect()
            raise DeviceError(f"Write failed: {e}") from e

    def read_packet(self, timeout_ms: int = 100) -> bytes | None:
        """Read one incoming packet. Returns None on timeout."""
        if not self._connected or not self._dev:
            return None
        try:
            if self.spec.transport == "serial":
                # Avoid reassigning .timeout unless it actually changes —
                # pyserial reconfigures the port (tcsetattr) on every set,
                # which is disruptive to reads at high baud rates.
                new_timeout = timeout_ms / 1000.0
                if self._dev.timeout != new_timeout:
                    self._dev.timeout = new_timeout
                data = self._dev.read(self.spec.packet_size)
            else:
                data = self._dev.read(self.spec.packet_size, timeout_ms=timeout_ms)
            if data:
                log.debug("RX: %s", bytes(data).hex(" "))
                return bytes(data)
        except (OSError, serial.SerialException) as e:
            self._connected = False
            if self._on_disconnect:
                self._on_disconnect()
            raise DeviceError(f"Read failed: {e}") from e
        return None

    def send_and_read(self, payload: bytes, retries: int = 3) -> bytes | None:
        """Send a packet and wait for a response."""
        self.send_packet(payload)
        for _ in range(retries):
            resp = self.read_packet(timeout_ms=200)
            if resp:
                return resp
            time.sleep(0.05)
        return None

    # ── Command channel (confirmed protocol, 33C3:7788 family) ──
    #
    # Frame format: [0x55, 0xAA, lenLo, lenHi, key, ...payload, sumLo, sumHi]
    #   len      = total frame length (payload length + 7 bytes of overhead)
    #   checksum = sum of all preceding bytes (sync+len+key+payload) & 0xFFFF,
    #              appended little-endian.
    # Same framing is used for responses; the payload is UTF-8 JSON at
    # bytes[5:-2]. Extracted from and verified against the vendor's own
    # Electron app (main/_baseClass/device.js, handleData()/listen()).

    @staticmethod
    def _build_command_frame(key: int, payload: bytes = b"") -> bytes:
        frame = bytes([0x55, 0xAA])
        total_len = len(payload) + 7
        frame += bytes([total_len % 256, total_len // 256, key]) + payload
        checksum = sum(frame) & 0xFFFF
        frame += bytes([checksum & 0xFF, (checksum >> 8) & 0xFF])
        return frame

    def _read_command_response(self, timeout_ms: int = 3000) -> bytes | None:
        """
        Read one full [0x55,0xAA,...] response frame off the serial link.

        NOTE: two things about this device's USB-CDC link at 2,000,000 baud
        turned out to matter a lot (found by testing against real hardware):
        - pyserial reconfigures the port (tcsetattr) every time `.timeout`
          is assigned, which is disruptive if done inside a read loop.
        - issuing back-to-back separate read() syscalls right after writing
          (e.g. "read 5 bytes for the header, then read N more") reliably
          returned a *shorter-than-actual* frame with a garbled length
          field — some part of the response gets lost between the two
          syscalls. Waiting briefly and doing a single bulk read of
          whatever has arrived is reliable; the length field in the frame
          is then only used to sanity-check what we already have, not to
          drive a second precise read.
        """
        if self.spec.transport != "serial" or not self._dev:
            return None
        self._dev.timeout = timeout_ms / 1000.0
        time.sleep(0.15)
        buf = self._dev.read(4096)
        if len(buf) < 7 or buf[0] != 0x55 or buf[1] != 0xAA:
            return None
        return buf

    def send_command(self, key: int, payload: bytes = b"",
                      expect_response: bool = True, timeout_ms: int = 3000) -> dict | None:
        """
        Send a command-channel frame and (optionally) parse the JSON response.
        Only implemented for transport="serial" on the confirmed protocol family.
        """
        frame = self._build_command_frame(key, payload)
        self.send_packet(frame)
        if not expect_response:
            return None
        raw = self._read_command_response(timeout_ms=timeout_ms)
        if raw is None:
            raise DeviceError(f"No response to command 0x{key:02X}")
        body = raw[5:-2]
        try:
            return json.loads(body.decode("utf8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"raw": body.hex()}

    def get_info(self) -> dict:
        """Query device info: width, height, brightness, model, version, uid, region."""
        resp = self.send_command(self.spec.cmd_get_info)
        return (resp or {}).get("data", {})

    def set_brightness(self, percent: int) -> None:
        """Set LCD backlight brightness, 0-100."""
        percent = max(0, min(100, percent))
        self.send_command(self.spec.cmd_set_brightness, bytes([percent]), expect_response=False)

    def restart(self) -> None:
        """Restart the device's LCD controller."""
        self.send_command(self.spec.cmd_restart, expect_response=False)

    def __repr__(self) -> str:
        state = "connected" if self._connected else "disconnected"
        return f"<AIODevice {self.spec.name} VID:PID=0x{self.spec.vid:04X}:0x{self.spec.pid:04X} [{state}]>"


# ──────────────────────────────────────────────────────────────
#  Device Discovery
# ──────────────────────────────────────────────────────────────

def find_serial_port(vid: int, pid: int) -> str | None:
    """Find the serial port path (e.g. /dev/ttyACM0, COM3) for a VID:PID."""
    for p in list_ports.comports():
        if p.vid == vid and p.pid == pid:
            return p.device
    return None


def find_devices(extra_specs: list[DeviceSpec] | None = None) -> list[AIODevice]:
    """
    Scan connected USB HID *and* USB-serial (CDC-ACM) devices and return
    AIODevice instances for any that match KNOWN_DEVICES.
    """
    specs = KNOWN_DEVICES + (extra_specs or [])
    connected_hids = {
        (d["vendor_id"], d["product_id"])
        for d in hid.enumerate()
    }
    connected_serial = {
        (p.vid, p.pid) for p in list_ports.comports() if p.vid is not None
    }
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
    Return raw HID + serial enumeration entries that look like they could
    be an AIO controller — for the GUI's device discovery UI.
    """
    candidates = []
    keywords = ["cooler", "aio", "pump", "lcd", "cool", "lovi", "rt-thread", "rtt"]

    for d in hid.enumerate():
        prod = (d.get("product_string") or "").lower()
        mfr = (d.get("manufacturer_string") or "").lower()
        usage_page = d.get("usage_page", 0)
        score = sum(10 for kw in keywords if kw in prod or kw in mfr)
        if usage_page >= 0xFF00:
            score += 5
        if score > 0:
            candidates.append({**d, "_transport": "hid", "_score": score})

    for p in list_ports.comports():
        if p.vid is None:
            continue
        prod = (p.product or p.description or "").lower()
        mfr = (p.manufacturer or "").lower()
        score = sum(10 for kw in keywords if kw in prod or kw in mfr)
        # A USB-serial device with a real VID/PID (not a system UART) is
        # already a decent candidate — these are invisible to hid.enumerate().
        score += 3
        if score > 0:
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
