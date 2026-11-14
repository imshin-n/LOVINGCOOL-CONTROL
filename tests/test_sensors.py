"""Sensors: locate once in a fake /sys, read only what's asked, cache for a second."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from lovingcool_control.core import sensors as sensors_mod
from lovingcool_control.core.sensors import Sensors


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def sysfs(tmp_path):
    """A /sys shaped like a Ryzen desktop with an iGPU and a discrete AMD GPU."""
    hw = tmp_path / "class/hwmon"
    write(hw / "hwmon0/name", "nvme\n")
    write(hw / "hwmon0/temp1_input", "40000\n")
    write(hw / "hwmon5/name", "k10temp\n")
    write(hw / "hwmon5/temp1_label", "Tctl\n")
    write(hw / "hwmon5/temp1_input", "52125\n")
    write(hw / "hwmon5/temp3_label", "Tccd1\n")
    write(hw / "hwmon5/temp3_input", "47000\n")
    drm = tmp_path / "class/drm"
    for card, vram, busy, edge in (("card0", 536870912, 3, 41000), ("card1", 17095983104, 27, 58000)):
        dev = drm / card / "device"
        write(dev / "gpu_busy_percent", f"{busy}\n")
        write(dev / "mem_info_vram_total", f"{vram}\n")
        write(dev / "hwmon/hwmon9/temp1_label", "edge\n")
        write(dev / "hwmon/hwmon9/temp1_input", f"{edge}\n")
    write(drm / "card1-DP-1/device/uevent", "")      # connector entries are ignored
    return tmp_path


def test_cpu_temp_prefers_tccd1(sysfs):
    m = Sensors(sys_root=sysfs).read({"cpu_temp"})
    assert m["cpu_temp"].available and m["cpu_temp"].value == pytest.approx(47.0)
    assert m["cpu_temp"].display == "47°C"


def test_cpu_temp_is_averaged_so_spikes_dont_show(sysfs, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(sensors_mod.time, "monotonic", lambda: clock[0])
    s = Sensors(sys_root=sysfs, refresh_s=1.0)
    for i in range(30):                       # 3 s of reads at 10 a second, alternating 38 / 54 °C
        write(sysfs / "class/hwmon/hwmon5/temp3_input", "38000\n" if i % 2 else "54000\n")
        value = s.read({"cpu_temp"})["cpu_temp"].value
        clock[0] += 0.1
    assert value == pytest.approx(46.0, abs=0.6)
    for _ in range(40):                       # a steady reading takes over within the window
        write(sysfs / "class/hwmon/hwmon5/temp3_input", "40000\n")
        value = s.read({"cpu_temp"})["cpu_temp"].value
        clock[0] += 0.1
    assert value == pytest.approx(40.0)


def test_cpu_temp_falls_back_to_tctl_without_a_ccd_sensor(sysfs):
    for f in ("temp3_label", "temp3_input"):
        (sysfs / "class/hwmon/hwmon5" / f).unlink()
    assert Sensors(sys_root=sysfs).read({"cpu_temp"})["cpu_temp"].value == pytest.approx(52.125)


def test_amd_gpu_picks_discrete_card(sysfs):
    m = Sensors(sys_root=sysfs).read({"gpu_percent", "gpu_temp"})
    assert m["gpu_percent"].value == 27 and m["gpu_temp"].value == pytest.approx(58.0)


def test_only_requested_keys_are_read(sysfs, monkeypatch):
    s = Sensors(sys_root=sysfs)
    computed = []
    real = s._compute
    monkeypatch.setattr(s, "_compute", lambda key: computed.append(key) or real(key))
    out = s.read({"clock", "ram_percent", "bogus"})
    assert set(out) == {"clock", "ram_percent"}
    assert computed == ["ram_percent"]


def test_values_are_cached_for_the_refresh_interval(sysfs):
    s = Sensors(sys_root=sysfs, refresh_s=0.2)
    assert s.read({"cpu_temp"})["cpu_temp"].value == pytest.approx(47.0)
    write(sysfs / "class/hwmon/hwmon5/temp3_input", "70000\n")
    assert s.read({"cpu_temp"})["cpu_temp"].value == pytest.approx(47.0)   # cached
    time.sleep(0.25)
    assert s.read({"cpu_temp"})["cpu_temp"].value == pytest.approx((47.0 + 70.0) / 2)   # averaged


def test_clock_is_always_current(sysfs):
    s = Sensors(sys_root=sysfs, refresh_s=60)
    a = s.read({"clock"})["clock"].value
    time.sleep(1.05)
    assert s.read({"clock"})["clock"].value != a


def test_missing_sensors_are_na_not_errors(tmp_path):
    m = Sensors(sys_root=tmp_path).read({"cpu_temp", "gpu_percent", "gpu_temp"})
    assert m["gpu_percent"].display == "N/A" and m["gpu_temp"].display == "N/A"


def test_read_all_keeps_old_interface():
    m = sensors_mod.read_all()
    assert set(m) == sensors_mod.ALL_KEYS
    assert all(hasattr(v, "display") for v in m.values())


def test_scene_metric_keys():
    from lovingcool_control.core.canvas import SceneConfig, Widget, scene_metric_keys

    cfg = SceneConfig(widgets=[Widget(kind="stat", metric_key="cpu_temp"), Widget(kind="text", text="x"),
                               Widget(kind="cpu_graph")])
    assert scene_metric_keys(cfg) == {"cpu_temp", "cpu_percent"}


def test_service_does_not_load_opencv_or_numpy_until_needed():
    code = ("import sys, lovingcool_control.service.daemon, lovingcool_control.gui.bridge;"
            "print('cv2' in sys.modules, 'numpy' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"]
