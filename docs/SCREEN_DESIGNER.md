# Screen Designer

A customizable live "dashboard" screen, separate from the simple LCD
Display tab — layers a background with hardware-stat overlays and streams
the composited result to the LCD continuously (1-30 fps).

**Canvas size is automatic**: both this page and the LCD Display page use
the panel size the connected device reports (through the service), or the
last size seen while it's disconnected. A label under the preview says
which.

**Background** — one of:
- **Solid Color**
- **Image** (PNG/JPG/BMP)
- **GIF** — animated, frame timing from the GIF itself
- **Video** (mp4/avi/mov/mkv/webm, via `opencv-python-headless`): decoded
  lazily and downscaled to the panel as it plays, so any length or
  resolution uses the same small amount of memory

**Fit mode** (image/GIF/video sources): **Cover** (crop to fill, default),
**Contain** (letterbox, nothing cropped), **Stretch** (distort to fill
exactly), or **Center** (no scaling at all).

**Transparent backgrounds**: if the source has an alpha channel, it's
composited onto a **fill color** you pick (separate from the solid-background
color) instead of showing black. The same fill color is used for corners
introduced by rotation and by Contain/Center's letterboxing.

**Orientation — Landscape / Portrait**: a proper orientation swap, not just
a rotated crop. The LCD panel's raster is a fixed physical shape (e.g.
480x320) — Portrait mode composes your background and widgets on a
*swapped-aspect* logical canvas (320x480) so the preview and drag-editing
feel like designing a normal tall screen, then rotates that whole
composition 90° back into the panel's native 480x320 buffer only at the
moment a frame is sent. Switching orientation rescales existing widget
positions proportionally so nothing ends up off-canvas. Implementation:
`logical_canvas_size()` and `orient_for_device()` live in `core/lcd.py`
(not `core/canvas.py`) precisely so both editing screens can share one
implementation — `LCDController.send_image()` on the simple LCD Display
tab uses them directly, and `core.canvas.compose_scene()` here uses them
to render at the logical size (what `ScenePreview` shows and what drag
hit-testing uses) before `orient_for_device()` does the final rotate/fit
to the device's fixed native size. Verified live against real hardware in
both orientations, on both editing screens (see below).

**Fine rotation**: a separate continuous slider (0.0°-360.0°) applies *on
top of* orientation, within the logical canvas, for small-angle mounting
adjustments or effects — set it to exactly 180° to flip a Portrait layout
if your panel ends up mounted upside-down relative to what you designed.

**Widgets — click-drag directly in the preview to reposition them** (no more
typing X/Y by hand, though the Add/Edit dialog still supports exact values
if you want them). Click a widget to select it (a white outline appears in
the preview and the matching row highlights in the list); drag to move;
`Delete`/`Backspace` removes the selection. An optional **10px grid-snap**
checkbox keeps things aligned. Each widget is independently positioned,
colored, and sized, and can be **Edit**ed or **Duplicate**d after creation,
not just added/removed:
- **Custom Text** — static label
- **Hardware Stat** — live-updating readout, sourced from `core/sensors.py`:
  CPU usage %, CPU temperature, CPU frequency, RAM usage %, disk usage %,
  GPU usage % / temperature (NVIDIA via `nvidia-smi`, if present), battery
  %, network sent/received (session totals), system uptime, clock, date.
- **CPU Graph** — a live sparkline/history graph of CPU usage over the last
  ~25 seconds, gauge-style with a rounded border. This is the "cool
  feature" — most cheap AIO LCD tools only show a static number; this
  shows the trend.

**Windows compatibility**: every metric in `core/sensors.py` is guarded so
a missing capability degrades to "N/A" instead of crashing — this matters
because `psutil`'s temperature/fan sensors (`sensors_temperatures()`,
`sensors_fans()`) are **Linux/macOS-only**; they don't exist on Windows at
all. CPU/RAM/disk/battery/network/uptime all work identically on Windows
via `psutil`. For CPU temperature specifically on Windows, there's a
best-effort fallback to a WMI query (`root\WMI` /
`MSAcpi_ThermalZoneTemperature`, optional `wmi` package) — this only works
on some systems (often needs LibreHardwareMonitor or similar running as a
WMI provider); when it doesn't, the widget just shows "N/A" rather than
erroring. GPU stats depend on `nvidia-smi` being on `PATH`, which the
NVIDIA Windows driver installs by default for NVIDIA cards; no AMD/Intel
GPU support currently.

**Scene files**: "Save As..." / "Open..." store the whole scene (background,
fit mode, colours, orientation, rotation, fps and every widget) as JSON,
by default in `~/.config/lovingcool-control/scenes/`. Run a saved scene
without the GUI: `lovingcool-ctl scene <file.json>`. Your work in progress
is also autosaved to `scenes/designer.json` and reopened next time.

**Start Live** saves the scene to `scenes/designer-live.json` and asks the
service to run it, so it keeps running after you close the window (with the
background service installed). While live, every edit (dragging a widget,
changing a colour, adding text) is pushed to the screen once it has been
stable for about half a second. "Stop Live" turns the screen off.
