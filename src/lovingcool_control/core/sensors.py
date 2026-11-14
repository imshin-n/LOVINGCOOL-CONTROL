"""
lovingcool-control :: core/sensors.py
=====================================
System stats for the Screen Designer's widgets: CPU/GPU usage and
temperature, CPU frequency, RAM, disk, network, battery, uptime, clock.

Built to be cheap, because a live scene asks several times a second:

- Only the stats a scene actually shows are read (read(keys)).
- Values other than the clock are refreshed at most once a second
  (REFRESH_S); faster than that just repeats the same numbers.
- On Linux the CPU and GPU sensors are located once in /sys and then read
  as a single small file each, instead of psutil.sensors_temperatures(),
  which reads every sensor on the machine (~85 files here) per call.
- GPU: NVIDIA via nvidia-smi (throttled like everything else), AMD via
  sysfs (gpu_busy_percent and the card's hwmon "edge" temperature).

Every stat degrades to "N/A" (Metric.available=False) when the platform,
hardware or an optional package doesn't provide it; nothing here raises.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
REFRESH_S = 1.0

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# CPU temperature drivers, best first, with the sensor label to prefer in each.
_CPU_HWMON = [("k10temp", ("tctl", "tdie")), ("zenpower", ("tdie", "tctl")),
              ("coretemp", ("package id 0",)), ("cpu_thermal", ()), ("acpitz", ())]
_CPU_TEMP_HINTS = ("tctl", "tdie", "package id 0", "cpu package", "core 0", "cpu")   # psutil fallback, best first


@dataclass
class Metric:
    """One stat: its key, how to label it, and the current value (None when unavailable)."""
    key: str
    label: str
    unit: str
    available: bool
    value: float | str | None = None

    @property
    def display(self) -> str:
        """The value as shown on screen: whole numbers plus unit, or "N/A"."""
        if self.value is None or not self.available:
            return "N/A"
        text = f"{round(self.value)}" if isinstance(self.value, float) else str(self.value)
        return text + self.unit


METRIC_CHOICES = [
    ("clock", "Time"),
    ("date", "Date"),
    ("cpu_percent", "CPU load"),
    ("cpu_temp", "CPU temperature"),
    ("cpu_freq", "CPU clock speed"),
    ("ram_percent", "Memory in use"),
    ("disk_percent", "Disk space used"),
    ("gpu_percent", "GPU load"),
    ("gpu_temp", "GPU temperature"),
    ("battery_percent", "Battery level"),
    ("net_sent", "Data sent since boot"),
    ("net_recv", "Data received since boot"),
    ("uptime", "Time since boot"),
]
ALL_KEYS = frozenset(k for k, _ in METRIC_CHOICES)

_LABELS = {
    "clock": ("Clock", ""), "date": ("Date", ""), "cpu_percent": ("CPU", "%"), "cpu_temp": ("CPU Temp", "°C"),
    "cpu_freq": ("CPU Freq", " GHz"), "ram_percent": ("RAM", "%"), "disk_percent": ("Disk", "%"),
    "gpu_percent": ("GPU", "%"), "gpu_temp": ("GPU Temp", "°C"), "battery_percent": ("Battery", "%"),
    "net_sent": ("Net Sent", " GB"), "net_recv": ("Net Recv", " GB"), "uptime": ("Uptime", ""),
}


def _read_number(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return None


class Sensors:
    """
    Reads stats on demand, with per-stat caching. One instance per consumer
    is fine; locating sensors happens once per instance.
    """

    def __init__(self, sys_root: Path = Path("/sys"), refresh_s: float = REFRESH_S):
        self.sys_root = sys_root
        self.refresh_s = refresh_s
        self._cache: dict[str, tuple[float, object]] = {}
        self._located = False
        self._cpu_temp_path: Path | None = None
        self._amd_busy_path: Path | None = None
        self._amd_temp_path: Path | None = None
        self._nvidia_smi = shutil.which("nvidia-smi")

    # ── Locating sensors (once) ───────────────────────────────

    def _locate(self) -> None:
        if self._located:
            return
        self._located = True
        if IS_WINDOWS:
            return
        self._cpu_temp_path = self._find_cpu_temp()
        self._amd_busy_path, self._amd_temp_path = self._find_amd_gpu()
        log.debug("CPU temp: %s, AMD GPU: %s / %s", self._cpu_temp_path, self._amd_busy_path, self._amd_temp_path)

    def _find_cpu_temp(self) -> Path | None:
        hwmons = {}
        for d in sorted((self.sys_root / "class/hwmon").glob("hwmon*")):
            try:
                hwmons.setdefault((d / "name").read_text().strip(), d)
            except OSError:
                continue
        for driver, labels in _CPU_HWMON:
            d = hwmons.get(driver)
            if d is None:
                continue
            inputs = sorted(d.glob("temp*_input"))
            for want in labels:
                for inp in inputs:
                    label = inp.with_name(inp.name.replace("_input", "_label"))
                    try:
                        if label.read_text().strip().lower() == want:
                            return inp
                    except OSError:
                        continue
            if inputs:
                return inputs[0]
        return None

    def _find_amd_gpu(self) -> tuple[Path | None, Path | None]:
        """The AMD card with the most VRAM (the discrete one, if there's also an iGPU)."""
        best: tuple[int, Path] | None = None
        for card in (self.sys_root / "class/drm").glob("card[0-9]*"):
            dev = card / "device"
            busy = dev / "gpu_busy_percent"
            if "-" in card.name or not busy.exists():
                continue
            vram = int(_read_number(dev / "mem_info_vram_total") or 0)
            if best is None or vram > best[0]:
                best = (vram, dev)
        if best is None:
            return None, None
        dev = best[1]
        temp = None
        for inp in sorted(dev.glob("hwmon/hwmon*/temp*_input")):
            label = inp.with_name(inp.name.replace("_input", "_label"))
            try:
                if label.read_text().strip().lower() == "edge":
                    temp = inp
                    break
            except OSError:
                pass
            temp = temp or inp
        return dev / "gpu_busy_percent", temp

    # ── Individual readers ────────────────────────────────────

    def _cpu_temp(self) -> float | None:
        if self._cpu_temp_path is not None:
            v = _read_number(self._cpu_temp_path)
            return v / 1000.0 if v is not None else None
        if IS_WINDOWS:
            return _wmi_cpu_temp()
        if PSUTIL_AVAILABLE and hasattr(psutil, "sensors_temperatures"):   # e.g. unusual drivers
            try:
                temps = psutil.sensors_temperatures()
            except Exception:
                return None
            readings = [(r.label or "").lower() for group in temps.values() for r in group if r.current]
            values = [r.current for group in temps.values() for r in group if r.current]
            best = min(range(len(readings)), default=None,
                       key=lambda i: next((rank for rank, word in enumerate(_CPU_TEMP_HINTS) if word in readings[i]),
                                          len(_CPU_TEMP_HINTS)))
            return values[best] if best is not None else None
        return None

    def _gpu(self) -> tuple[float | None, float | None]:
        if self._amd_busy_path is not None:
            busy = _read_number(self._amd_busy_path)
            temp = _read_number(self._amd_temp_path)
            return busy, (temp / 1000.0 if temp is not None else None)
        if self._nvidia_smi:
            try:
                out = subprocess.run([self._nvidia_smi, "--query-gpu=utilization.gpu,temperature.gpu",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
                if out.returncode == 0:
                    util, temp = out.stdout.strip().splitlines()[0].split(",")
                    return float(util), float(temp)
            except Exception:
                pass
        return None, None

    def _compute(self, key: str):
        """Fresh value for one key (None when unavailable). Paired keys share a reading."""
        try:
            if key == "cpu_percent":
                return psutil.cpu_percent(interval=None) if PSUTIL_AVAILABLE else None
            if key == "ram_percent":
                return psutil.virtual_memory().percent if PSUTIL_AVAILABLE else None
            if key == "cpu_temp":
                return self._cpu_temp()
            if key == "cpu_freq":
                f = psutil.cpu_freq() if PSUTIL_AVAILABLE else None
                return f.current / 1000.0 if f else None
            if key == "disk_percent":
                return psutil.disk_usage("C:\\" if IS_WINDOWS else "/").percent if PSUTIL_AVAILABLE else None
            if key == "battery_percent":
                b = psutil.sensors_battery() if PSUTIL_AVAILABLE and hasattr(psutil, "sensors_battery") else None
                return b.percent if b else None
            if key == "uptime":
                if not PSUTIL_AVAILABLE:
                    return None
                secs = int((timedelta(seconds=time.time() - psutil.boot_time())).total_seconds())
                return f"{secs // 3600}h {(secs % 3600) // 60}m"
        except Exception:
            return None
        return None

    def _pair(self, a: str, b: str, fn) -> None:
        now = time.monotonic()
        cached = self._cache.get(a)
        if cached and now - cached[0] < self.refresh_s:
            return
        try:
            va, vb = fn()
        except Exception:
            va = vb = None
        self._cache[a] = (now, va)
        self._cache[b] = (now, vb)

    def _value(self, key: str):
        if key in ("gpu_percent", "gpu_temp"):
            self._pair("gpu_percent", "gpu_temp", self._gpu)
            return self._cache[key][1]
        if key in ("net_sent", "net_recv"):
            def net():
                totals = psutil.net_io_counters()
                return totals.bytes_sent / 1e9, totals.bytes_recv / 1e9
            self._pair("net_sent", "net_recv", net if PSUTIL_AVAILABLE else (lambda: (None, None)))
            return self._cache[key][1]
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self.refresh_s:
            return cached[1]
        value = self._compute(key)
        self._cache[key] = (now, value)
        return value

    # ── Public ────────────────────────────────────────────────

    def read(self, keys=None) -> dict[str, Metric]:
        """Metrics for `keys` (all of them if None). The clock and date are always current."""
        self._locate()
        keys = ALL_KEYS if keys is None else (set(keys) & ALL_KEYS)
        now = datetime.now()
        out: dict[str, Metric] = {}
        for key in keys:
            label, unit = _LABELS[key]
            if key == "clock":
                value = now.strftime("%H:%M:%S")
            elif key == "date":
                value = now.strftime("%Y-%m-%d")
            else:
                value = self._value(key)
            out[key] = Metric(key, label, unit, value is not None, value)
        return out


def _wmi_cpu_temp() -> float | None:
    """Best effort on Windows; usually needs LibreHardwareMonitor running as a WMI provider."""
    try:
        import wmi
        zones = wmi.WMI(namespace="root\\WMI").MSAcpi_ThermalZoneTemperature()
        return (zones[0].CurrentTemperature / 10.0) - 273.15 if zones else None
    except Exception:
        return None


_default = Sensors()


def read_all(keys=None) -> dict[str, Metric]:
    """Shared, cached reader (see Sensors.read)."""
    return _default.read(keys)


class CpuHistory:
    """Recent CPU load values for the graph widget: one per stats refresh, about a minute's worth."""

    def __init__(self, max_points: int = 60):
        self.max_points = max_points
        self._points: deque[float] = deque(maxlen=max_points)
        self._last = float("-inf")

    def sample(self, cpu_percent: float | None) -> list[float]:
        """Record a value if a refresh interval has passed; return the history, oldest first."""
        now = time.monotonic()
        if cpu_percent is not None and now - self._last >= REFRESH_S * 0.95:
            self._points.append(float(cpu_percent))
            self._last = now
        return list(self._points)
