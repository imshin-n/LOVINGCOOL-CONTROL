"""
lovingcool-control :: core/sensors.py
==============================
Hardware monitoring for the Screen Designer's stat widgets (CPU/GPU
usage & temperature, RAM, disk, network, battery, uptime, clock).

Cross-platform via psutil where psutil supports it. A few metrics
(temperature sensors, fan speed) are Linux/macOS-only in psutil itself —
on Windows those fall back to a best-effort WMI query (optional `wmi`
package) and simply report "N/A" if that's not available either, rather
than crashing. Every metric degrades gracefully like this: missing
psutil, missing wmi, missing nvidia-smi, or a platform that just doesn't
expose a given sensor all result in Metric.available=False, never an
exception surfacing to the GUI.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

_WMI_AVAILABLE = False
if IS_WINDOWS:
    try:
        import wmi
        _WMI_AVAILABLE = True
    except ImportError:
        _WMI_AVAILABLE = False

_NVIDIA_SMI = shutil.which("nvidia-smi")

# Common CPU-package temperature sensor labels, in priority order (Linux hwmon naming).
_CPU_TEMP_HINTS = ["tctl", "package id 0", "cpu package", "tdie", "core 0", "cpu"]


@dataclass
class Metric:
    key: str
    label: str
    unit: str
    available: bool
    value: float | str | None = None

    @property
    def display(self) -> str:
        if not self.available or self.value is None:
            return "N/A"
        if isinstance(self.value, float):
            return f"{self.value:.0f}{self.unit}"
        return f"{self.value}{self.unit}"


# ── Individual metric readers (each fully self-guarded) ──────────

def _cpu_temp_c() -> float | None:
    if PSUTIL_AVAILABLE:
        try:
            temps = psutil.sensors_temperatures()   # AttributeError on Windows/some macOS
        except Exception:
            temps = None
        if temps:
            candidates = [e for entries in temps.values() for e in entries if e.current]
            for hint in _CPU_TEMP_HINTS:
                for e in candidates:
                    if hint in (e.label or "").lower():
                        return e.current
            if candidates:
                return candidates[0].current

    if _WMI_AVAILABLE:
        # Best-effort: many Windows systems don't expose this without
        # LibreHardwareMonitor/OpenHardwareMonitor running as a WMI provider.
        try:
            w = wmi.WMI(namespace="root\\WMI")
            zones = w.MSAcpi_ThermalZoneTemperature()
            if zones:
                # value is in tenths of Kelvin
                return (zones[0].CurrentTemperature / 10.0) - 273.15
        except Exception:
            pass
    return None


def _gpu_stats() -> tuple[float | None, float | None]:
    """Returns (utilization_percent, temperature_c) via nvidia-smi, or (None, None)."""
    if not _NVIDIA_SMI:
        return None, None
    try:
        out = subprocess.run(
            [_NVIDIA_SMI, "--query-gpu=utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode != 0:
            return None, None
        util_str, temp_str = out.stdout.strip().split(",")
        return float(util_str.strip()), float(temp_str.strip())
    except Exception:
        return None, None


def _disk_percent() -> float | None:
    if not PSUTIL_AVAILABLE:
        return None
    try:
        path = "C:\\" if IS_WINDOWS else "/"
        return psutil.disk_usage(path).percent
    except Exception:
        return None


def _battery_percent() -> float | None:
    if not PSUTIL_AVAILABLE:
        return None
    try:
        battery = psutil.sensors_battery()
        return battery.percent if battery else None
    except Exception:
        return None


def _net_totals_gb() -> tuple[float | None, float | None]:
    if not PSUTIL_AVAILABLE:
        return None, None
    try:
        io = psutil.net_io_counters()
        return io.bytes_sent / 1e9, io.bytes_recv / 1e9
    except Exception:
        return None, None


def _uptime_str() -> str | None:
    if not PSUTIL_AVAILABLE:
        return None
    try:
        delta = timedelta(seconds=time.time() - psutil.boot_time())
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes}m"
    except Exception:
        return None


def _cpu_freq_ghz() -> float | None:
    if not PSUTIL_AVAILABLE:
        return None
    try:
        freq = psutil.cpu_freq()
        return freq.current / 1000.0 if freq else None
    except Exception:
        return None


def read_all() -> dict[str, Metric]:
    """Snapshot every supported metric right now."""
    now = datetime.now()
    metrics: dict[str, Metric] = {
        "clock": Metric("clock", "Clock", "", True, now.strftime("%H:%M:%S")),
        "date": Metric("date", "Date", "", True, now.strftime("%Y-%m-%d")),
    }

    def _m(key: str, label: str, unit: str, value):
        metrics[key] = Metric(key, label, unit, value is not None, value)

    if PSUTIL_AVAILABLE:
        try:
            _m("cpu_percent", "CPU", "%", psutil.cpu_percent(interval=None))
        except Exception:
            _m("cpu_percent", "CPU", "%", None)
        try:
            _m("ram_percent", "RAM", "%", psutil.virtual_memory().percent)
        except Exception:
            _m("ram_percent", "RAM", "%", None)
    else:
        _m("cpu_percent", "CPU", "%", None)
        _m("ram_percent", "RAM", "%", None)

    _m("cpu_temp", "CPU Temp", "°C", _cpu_temp_c())

    gpu_util, gpu_temp = _gpu_stats()
    _m("gpu_percent", "GPU", "%", gpu_util)
    _m("gpu_temp", "GPU Temp", "°C", gpu_temp)

    _m("disk_percent", "Disk", "%", _disk_percent())
    _m("battery_percent", "Battery", "%", _battery_percent())
    _m("cpu_freq", "CPU Freq", " GHz", _cpu_freq_ghz())
    _m("uptime", "Uptime", "", _uptime_str())

    net_sent, net_recv = _net_totals_gb()
    _m("net_sent", "Net Sent", " GB", net_sent)
    _m("net_recv", "Net Recv", " GB", net_recv)

    return metrics


METRIC_CHOICES = [
    ("clock", "Clock (HH:MM:SS)"),
    ("date", "Date"),
    ("cpu_percent", "CPU Usage %"),
    ("cpu_temp", "CPU Temperature"),
    ("cpu_freq", "CPU Frequency"),
    ("ram_percent", "RAM Usage %"),
    ("disk_percent", "Disk Usage %"),
    ("gpu_percent", "GPU Usage %"),
    ("gpu_temp", "GPU Temperature"),
    ("battery_percent", "Battery %"),
    ("net_sent", "Network Sent (total)"),
    ("net_recv", "Network Received (total)"),
    ("uptime", "System Uptime"),
]


class CpuHistory:
    """Rolling CPU% history for the sparkline/graph widget."""

    def __init__(self, max_points: int = 60):
        self.max_points = max_points
        self._values: list[float] = []
        self._last_sample = 0.0

    def sample(self, cpu_percent: float | None) -> list[float]:
        now = time.monotonic()
        if cpu_percent is not None and now - self._last_sample > 0.4:
            self._values.append(cpu_percent)
            if len(self._values) > self.max_points:
                self._values.pop(0)
            self._last_sample = now
        return list(self._values)
