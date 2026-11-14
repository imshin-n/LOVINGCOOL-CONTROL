"""
lovingcool-control :: core/rgb.py
=========================
RGB lighting control.

IMPORTANT: unlike core/lcd.py, this module is still an UNVERIFIED STUB.
Investigating the vendor's own Electron app source (see README) showed
that RGB/fan lighting on this product line is driven by a SEPARATE USB
device from the LCD controller — the app looks for it on a completely
different VID/PID whitelist (commonly CH340-family USB-serial bridges:
VID 0x1A86, or 0xA012 / 0xAA12), using a different, simpler byte
protocol (raw arrays like [0x55, 0x15, 0x01], not the 0x55/0xAA/len/key/
checksum framing used by the LCD channel in core/device.py).

That second device did not enumerate on the machine this was developed
on — either this AIO unit doesn't have a separate RGB header wired to
USB, or it just wasn't connected. If you have one:
  1. Run `python tools/enumerate.py --watch` and plug in the second cable.
  2. Update KNOWN_DEVICES in core/device.py with its real VID:PID
     (transport="serial" almost certainly, but confirm with enumerate.py).
  3. Use tools/sniffer.py --transport serial to capture real command
     bytes, then fill in _build_packet() below — the public API
     (modes, colors, zones) is already wired up and shouldn't need to change.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum

from .device import AIODevice

log = logging.getLogger(__name__)


class RGBMode(Enum):
    OFF = "off"
    STATIC = "static"
    BREATHING = "breathing"
    RAINBOW = "rainbow"
    SPECTRUM_CYCLE = "spectrum_cycle"
    WAVE = "wave"
    PULSE = "pulse"


@dataclass
class RGBColor:
    r: int
    g: int
    b: int

    def to_bytes(self) -> bytes:
        return bytes([self.r & 0xFF, self.g & 0xFF, self.b & 0xFF])

    @classmethod
    def from_hex(cls, hex_str: str) -> RGBColor:
        hex_str = hex_str.lstrip("#")
        r = int(hex_str[0:2], 16)
        g = int(hex_str[2:4], 16)
        b = int(hex_str[4:6], 16)
        return cls(r, g, b)

    def to_hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"


# PLACEHOLDER — the RGB controller is separate hardware not present on
# the dev machine; this command byte is an unverified guess (see module
# docstring). Confirm/replace via tools/sniffer.py against the real device.
CMD_RGB_SET = 0x40

# Mode → protocol byte mapping (PLACEHOLDER — fill in after sniffing)
MODE_CODES = {
    RGBMode.OFF: 0x00,
    RGBMode.STATIC: 0x01,
    RGBMode.BREATHING: 0x02,
    RGBMode.RAINBOW: 0x03,
    RGBMode.SPECTRUM_CYCLE: 0x04,
    RGBMode.WAVE: 0x05,
    RGBMode.PULSE: 0x06,
}


class RGBController:
    """
    Manages RGB lighting state and transmission.

    Zone support: many AIOs have a single LED ring/zone on the pump head.
    If your device has multiple addressable zones, extend `zone_count`
    and update _build_packet to address each zone.
    """

    def __init__(self, device: AIODevice, zone_count: int = 1):
        self.device = device
        self.zone_count = zone_count
        self._mode = RGBMode.STATIC
        self._color = RGBColor(255, 0, 0)
        self._speed = 50          # 0-100
        self._brightness = 100    # 0-100
        self._anim_thread: threading.Thread | None = None
        self._stop_anim = threading.Event()

    # ── Packet building (STUB) ────────────────────────────────

    def _build_packet(
        self,
        mode: RGBMode,
        color: RGBColor,
        speed: int,
        brightness: int,
        zone: int = 0xFF,  # 0xFF = all zones
    ) -> bytes:
        """
        STUB: Build the raw command packet.
        Common pattern for OEM RGB controllers:
          [CMD_RGB_SET, zone, mode_code, r, g, b, speed, brightness, ...padding]
        """
        payload = bytes([
            CMD_RGB_SET,
            zone,
            MODE_CODES.get(mode, 0x01),
            color.r, color.g, color.b,
            speed,
            brightness,
        ] + [0x00] * 56)
        return payload

    # ── Public API ────────────────────────────────────────────

    def set_static(self, color: RGBColor, brightness: int = 100, zone: int = 0xFF) -> None:
        """Set a solid static color."""
        self.stop_animation()
        self._mode = RGBMode.STATIC
        self._color = color
        self._brightness = brightness
        packet = self._build_packet(RGBMode.STATIC, color, 0, brightness, zone)
        self.device.send_packet(packet)
        log.info("RGB static color set: %s", color.to_hex())

    def set_mode(
        self,
        mode: RGBMode,
        color: RGBColor | None = None,
        speed: int = 50,
        brightness: int = 100,
        zone: int = 0xFF,
    ) -> None:
        """Set a built-in lighting effect mode (breathing, rainbow, etc.)."""
        self.stop_animation()
        self._mode = mode
        if color:
            self._color = color
        self._speed = speed
        self._brightness = brightness
        packet = self._build_packet(mode, self._color, speed, brightness, zone)
        self.device.send_packet(packet)
        log.info("RGB mode set: %s", mode.value)

    def turn_off(self) -> None:
        """Turn off all RGB lighting."""
        self.stop_animation()
        packet = self._build_packet(RGBMode.OFF, RGBColor(0, 0, 0), 0, 0)
        self.device.send_packet(packet)
        log.info("RGB turned off")

    def set_brightness(self, brightness: int) -> None:
        """Adjust brightness without changing mode/color (0-100)."""
        self._brightness = max(0, min(100, brightness))
        packet = self._build_packet(self._mode, self._color, self._speed, self._brightness)
        self.device.send_packet(packet)

    # ── Software-driven animations (for effects not built into firmware) ──

    def play_custom_animation(
        self,
        color_sequence: list[RGBColor],
        frame_duration_ms: int = 50,
        loop: bool = True,
    ) -> None:
        """
        Drive a custom color sequence from the host (software animation),
        useful for effects the firmware doesn't natively support.
        """
        self.stop_animation()
        self._stop_anim.clear()

        def _animate():
            while not self._stop_anim.is_set():
                for color in color_sequence:
                    if self._stop_anim.is_set():
                        break
                    packet = self._build_packet(RGBMode.STATIC, color, 0, self._brightness)
                    self.device.send_packet(packet)
                    self._stop_anim.wait(timeout=frame_duration_ms / 1000.0)
                if not loop:
                    break

        self._anim_thread = threading.Thread(target=_animate, daemon=True, name="rgb-animator")
        self._anim_thread.start()

    def stop_animation(self) -> None:
        self._stop_anim.set()
        if self._anim_thread and self._anim_thread.is_alive():
            self._anim_thread.join(timeout=2.0)
        self._anim_thread = None

    @property
    def current_state(self) -> dict:
        return {
            "mode": self._mode.value,
            "color": self._color.to_hex(),
            "speed": self._speed,
            "brightness": self._brightness,
        }


def generate_rainbow_sequence(steps: int = 60) -> list[RGBColor]:
    """Generate a smooth rainbow color sequence for custom animations."""
    import colorsys
    colors = []
    for i in range(steps):
        hue = i / steps
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        colors.append(RGBColor(int(r * 255), int(g * 255), int(b * 255)))
    return colors
