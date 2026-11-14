# LOVINGCOOL-CONTROL

A cross-platform, open-source Python GUI for **Lovi.Cool AIO liquid coolers**
with an LCD display — built because no official Linux software exists.

Controls: LCD image / GIF / text display, RGB lighting (color, modes, brightness).

Runs on **Linux, Windows, and macOS** from the same codebase (PyQt6 + hidapi).

> **Origins:** this project started from
> [newan0805/loving_cool_aio_display_manage](https://github.com/newan0805/loving_cool_aio_display_manage)
> ("lovi-ctrl"), whose author reverse-engineered the LCD protocol documented below.
> It is being overhauled into a separate project; parts of that original code are
> still in use while they're replaced.

---

## ✅ LCD protocol status: confirmed & working (33C3:7788 / "txm818-7inch")

For the unit this project was developed against, the LCD protocol is no
longer a placeholder — it's implemented in `core/device.py` (command
channel) and `core/lcd.py` (image streaming), and confirmed both by
querying the live device and by displaying an image on the physical
screen through this app's own GUI.

---

## How it works — technical deep dive

### 1. The actual hardware doesn't match the original assumptions

This project originally assumed a USB **HID** device. The real controller
board enumerates as a plain **USB-CDC virtual serial port**:

```
Bus 003 Device 004: ID 33c3:7788 RT-Thread Team. RTT Virtual Serial
  bDeviceClass  2 (Communications)  bDeviceSubClass 2 (Abstract modem)
  -> shows up as /dev/ttyACM0 on Linux, a COM port on Windows
```

`hid.enumerate()` never sees it — it isn't HID at all. `core/device.py`'s
`DeviceSpec.transport` field ("hid" vs "serial") and `tools/enumerate.py`
scanning both `hid.enumerate()` and `serial.tools.list_ports.comports()`
exist specifically because of this: whichever list your device shows up
in tells you which transport to use.

### 2. Finding the real protocol: extracting the vendor's own source, not sniffing packets

Lovi.Cool (rebranded firmware from **Dongguan Hongtai Technology**,
product name **"LOVINGCOOL MONITOR"**) doesn't publish a spec, and the
device isn't in `liquidctl`. The usual approach for this kind of problem
is a USB packet capture against the official Windows app (Wireshark +
USBPcap, or a Wine + `socat` MITM bridge — both discussed further down).
That wasn't needed here, because the Windows installer turned out to be
an **Electron app**, and Electron ships its entire JS source in a
plaintext-recoverable archive:

```
Screen Driver Software.exe   (NSIS self-extracting installer, ~367MB)
  └─ $PLUGINSDIR/app-64.7z   (nested 7z archive — the real payload)
       └─ resources/app.asar (Electron's packed-source-files format)
            └─ main/_baseClass/device.js      <- LCD protocol, confirmed here
            └─ main/_baseClass/serialport.js  <- SerialPort connection params
            └─ main/_baseClass/device_led.js  <- separate RGB/LED protocol
```

All three archive layers (NSIS, 7z, asar) are standard, well-documented
formats with existing open-source tools — no need to run the untrusted
binary at all:

```bash
7z x "Screen Driver Software.exe" -o extracted/
7z x "extracted/\$PLUGINSDIR/app-64.7z" -o app64/
npx --yes asar extract app64/resources/app.asar app_src/
grep -rl "SerialPort\|handleData" app_src/main
# minified single-line JS -> readable:
npx --yes prettier --parser babel app_src/main/_baseClass/device.js > device_pretty.js
```

The minified variable names (`_0x1a2b3c`-style) are unreadable, but string
literals, numeric opcodes, and structure survive minification intact —
`grep`-ing for `Buffer.from(`, `0x55, 0xaa`, `baudRate`, and the various
`write({key: 0x..})` call sites was enough to reconstruct the whole
protocol without needing to deobfuscate variable names at all.

If you have the installer for a *different* Lovi.Cool / LOVINGCOOL /
Dongguan Hongtai model, the same steps will very likely work — check
`main/constant/appConstant.js` for a `vidWhiteList`/`pidWhiteList` entry
matching your device to confirm you're looking at the right brand config
(this app's shared codebase serves several rebrands: LOVINGCOOL, DELTA
CENTRO, Deep_Colors, etc., selected by `vidWhiteList`/`pidWhiteList` pairs).

### 3. Transport parameters

Confirmed from `serialport.js`'s `createClientInstance()`:

| Parameter | Value | Notes |
|---|---|---|
| Baud rate | **2,000,000** (`0x1e8480`) | Not a typo — this MCU-based panel really does run the UART-over-USB link at 2 Mbaud. |
| DTR | **must be asserted** (`dtr: true`) | Some MCU USB-CDC stacks gate the link on DTR; without it the device accepts writes but the read side stays silent. |
| autoOpen | false (app opens explicitly after construction) | Not meaningful in Python; `AIODevice.connect()` opens immediately. |

### 4. Command channel — `[0x55, 0xAA, ...]` framed protocol

Confirmed from `device.js`'s `handleData()` (request framing) and
`listen()` (response parsing):

```
Request/response frame:
  byte 0-1   0x55 0xAA                sync bytes
  byte 2-3   len (little-endian u16)  total frame length = payload length + 7
  byte 4     key                      command opcode
  byte 5..   payload                  command-specific bytes
  last 2     checksum (LE u16)        sum of ALL preceding bytes, & 0xFFFF
```

Responses use the identical framing; the payload (`bytes[5:-2]`) is a
UTF-8 JSON string, e.g. querying `key=0x06` on the real device returned:

```json
{"cmd":"info","data":{"width":480,"height":320,"diplay_on":true,
 "brightness":80,"i_blocks":204560,"i_block_size":512,"i_block_free":198496,
 "i_path":"/data","e_blocks":0,"e_block_size":0,"e_block_free":0,
 "e_path":"/sdcard","model":"txm818-7inch","version":"Ver1.0",
 "shape":"0BB1","uid":"0C014F018787","region":"00000"}}
```

Confirmed command opcodes (`DeviceSpec` fields in `core/device.py`):

| Opcode | Name | Payload | Notes |
|---|---|---|---|
| `0x01` | restart | none | fire-and-forget |
| `0x03` | setBrightness | `[percent]` (1 byte, 0-100) | screen backlight, not RGB |
| `0x06` | getInfo | none | returns the JSON blob above |
| `0x11` | commit / start-live | none | sent once before streaming image frames |
| `0x14` | setMotionBeforeOffScreen | `[mode, timeout]` | not wired up in this project |
| `0x15` | setMotionTimeout | `[mode, timeout]` | firmware version-gated (>= 2.8) in vendor app |
| `0x21` | close | none | firmware version-gated (>= 3.1) in vendor app |
| `0x23` | setSerialNum | bytes | not wired up |
| `0x25` | setMotor | `[0\|1]` | only relevant to a "sulcs" region variant, not this model |
| `0x26` | setRealTimePlayTimeout | `[seconds]` | firmware version-gated (>= 4.1) |

### 5. Image channel — separate from the command channel

The LCD image itself is **not** sent through the `0x55/0xAA` command
framing. Confirmed from `sendPic()`/`getSizeBt()`/`keepSendPic` in
`device.js`:

1. Resize the source frame to the panel's native resolution (`480x320`
   for this unit; query `key=0x06` to get yours).
2. JPEG-encode it, stepping quality down by 2 per retry until the output
   fits under a size budget (device/firmware-dependent, ~50KB default for
   this unit — see `DeviceClass.DEVICE_CONFIG` in `device.js` for the
   handful of model-specific overrides the vendor app hardcodes).
3. Write the raw JPEG bytes **directly to the serial port** — no chunk
   headers, no report IDs. Firmware versions reporting `> 2.8` additionally
   wrap the JPEG in `[4-byte LE length][jpeg bytes][2-byte LE checksum]`;
   this unit reports firmware `"Ver1.0"`, which fails that `> 2.8` check
   (note: it's a **string** compared with `>`, so a version like `"Ver1.0"`
   never satisfies any of the app's numeric version gates — JS coerces the
   comparison to `NaN`, which is always `false`), so frames go out as bare
   JPEG bytes.
4. Before the first frame, send command `0x11` once ("commit"/start-live).

`core/lcd.py`'s `LCDController` mirrors this exactly: `encode_jpeg_capped()`
does the quality search, `_build_jpeg_frame()` conditionally adds the
length+checksum wrapper based on a parsed firmware version, and
`send_raw_jpeg_frame()` calls `_ensure_live()` (the one-time `0x11`) before
writing.

### 6. Debugging gotchas found the hard way

These cost real back-and-forth against the live device before the driver
was reliable — worth knowing if you're extending this:

- **pyserial reconfigures the port on every `.timeout =` assignment.**
  `Serial.timeout` is a property whose setter calls `tcsetattr()`
  internally. Reassigning it inside a read-retry loop (once per loop
  iteration) was enough to corrupt reads at 2,000,000 baud. Fix: set
  `.timeout` once per logical read operation, not per retry.
- **Two back-to-back `read()` syscalls right after a write were
  unreliable at this baud rate.** The natural approach — read exactly 5
  header bytes, parse the declared length, then read exactly that many
  more bytes — intermittently returned a short/garbled frame (the
  device's *actual* transmission was sometimes longer than its own
  declared header length, e.g. observed 325 real bytes vs. a header
  claiming 275). What's reliable: sleep ~150ms after writing, then issue
  **one** bulk `read(4096)` and take whatever came back, rather than
  trusting the header's length field to drive a second precise read. See
  `AIODevice._read_command_response()` for the working implementation and
  its docstring for the blow-by-blow.
- **`dsrdtr=True` at construction, not just `.dtr = True` after open** —
  belt-and-suspenders for hardware flow control; didn't turn out to be
  the actual fix for the above, but kept since it matches the working
  reference implementation and doesn't hurt.

### 7. RGB / fan lighting — separate hardware, not yet wired up

`device_led.js` in the same vendor app source shows RGB/fan lighting is
driven by a **completely separate USB device** from the LCD controller —
filtered by its own VID/PID whitelist (`allLedVidWhiteList = ["A012",
"1A86", "AA12"]`, i.e. commonly CH340-family USB-serial bridges), using a
simpler, different byte protocol (raw arrays like `[0x55, 0x15, 0x01]` and
`[0x55, 0x25, 0x21, 0x00, 0x00, 0x00]`, not the checksum-framed command
channel above). This second device did not enumerate on the machine this
was developed on. `core/rgb.py` documents this and is left as a stub —
if you have a second USB cable from your AIO for the RGB/pump lighting,
`tools/enumerate.py --watch` while plugging it in will reveal its VID:PID,
and `tools/sniffer.py --transport serial` can capture the real bytes the
same way the LCD channel was being probed before the source-extraction
approach panned out.

If your own unit has a *different* VID:PID than `33C3:7788`, everything
above is still a strong starting point (same OEM, likely same protocol
family) — but treat it as unconfirmed until you verify with
`tools/sniffer.py` or by extracting your own installer's `app.asar`.

---

## Background: for devices where the protocol is still unknown

Everything below is the general reverse-engineering workflow this project
was originally built around, for anyone whose unit doesn't match the
confirmed device above. This is normal for community AIO drivers —
`liquidctl` itself supports dozens of devices this exact way. The good
news: once you find these
bytes, you only need to edit **3 functions** in 2 files
(`core/lcd.py` and `core/rgb.py`) — the rest of the app (GUI, image
processing, GIF playback, threading) is fully working already.

---

## Project structure

```
lovingcool-control/
├── src/lovingcool_control/core/
│   ├── device.py     # USB HID/serial connection, device registry, command channel
│   ├── lcd.py         # Image/GIF/text → JPEG frames → USB
│   ├── canvas.py       # Screen Designer: scene composer + live playback thread
│   ├── sensors.py        # CPU/GPU/RAM/temp readings for stat widgets
│   └── rgb.py              # RGB color/mode/brightness control (unverified, see below)
├── src/lovingcool_control/gui/
│   ├── app.py           # Main window, run this to launch the GUI
│   ├── theme.py          # Dark UI stylesheet
│   └── pages/
│       ├── device_page.py       # Connect/discover devices
│       ├── lcd_page.py            # Simple single-shot LCD editor
│       ├── screen_designer.py       # Customizable live screen builder
│       └── rgb_page.py                # RGB editor
├── tools/
│   ├── enumerate.py   # Step 1: find your AIO's VID:PID
│   └── sniffer.py       # Step 2: capture USB protocol bytes
├── udev/99-lovicool.rules  # Linux: non-root USB access
├── packaging/
│   ├── linux/               # systemd --user service (auto-start/auto-restart)
│   └── windows/                # Task Scheduler + restart-loop equivalent
├── build_linux.sh          # Build a standalone Linux executable (dist/lovingcool-control)
├── build_windows.bat       # Build a standalone Windows executable (dist\lovingcool-control.exe)
├── tests/                  # pytest suite (no hardware needed): uv run pytest
└── pyproject.toml          # dependencies + entry points (managed with uv)
```

---

## Setup

Needs [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

**Linux only** — allow USB access without sudo:
```bash
sudo cp udev/99-lovicool.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# unplug/replug the AIO
```

---

## Step 1 — Find your device's VID:PID

With the AIO plugged in:

```bash
uv run python tools/enumerate.py
```

This lists all USB HID devices and highlights likely candidates (vendor-specific
usage pages, name keywords like "cool"/"lcd"/"aio"). If nothing obvious shows
up, use watch mode and physically unplug/replug the AIO:

```bash
uv run python tools/enumerate.py --watch
```

Once you have the VID:PID (e.g. `0x0483:0x5750`), add it to your own
device file, no code changes needed:

- Linux: `~/.config/lovingcool-control/devices.toml`
- Windows: `%APPDATA%\lovingcool-control\devices.toml`
- macOS: `~/Library/Application Support/lovingcool-control/devices.toml`

Set `transport = "serial"` if your device showed up under "USB-serial
(CDC-ACM) devices" instead of the HID list:

```toml
[[device]]
name = "Lovi.Cool AIO 240"
vid = 0x0483          # your real VID
pid = 0x5750          # your real PID
transport = "serial"  # or "hid", depending on which list it appeared in
baudrate = 2_000_000
lcd_width = 240
lcd_height = 240
lcd_color_format = "JPEG"
```

Entries there are merged with the built-in list in
`src/lovingcool_control/core/devices.toml` (same VID:PID replaces the
built-in entry), which documents every field and already contains the
`0x33C3:0x7788` 7-inch unit and the `0x33C3:0x7791` SC60.

---

## Step 2 — Reverse-engineer the protocol

This is the real work. You need a way to see what the **official Windows
software** sends to the device, since that's the only known-working
reference. Two approaches, easiest first:

### Approach A — USB packet capture (recommended)

1. Install [Wireshark](https://www.wireshark.org/) with USBPcap (Windows) or
   usbmon (Linux, built into the kernel).
2. Run the official Lovi.Cool/manufacturer software in a Windows VM (or dual
   boot) with the capture running.
3. Make **one change at a time** — set LCD to a solid red image, then static
   RGB red, then breathing mode — and label each capture.
4. Compare byte sequences between captures to isolate which bytes control
   color, mode, image data, etc. (this is the standard technique documented
   in community AIO/RGB reverse-engineering guides).

### Approach B — Use this project's sniffer for live HID/serial inspection

`tools/sniffer.py` works over both transports — it tries HID first, then
falls back to USB-serial automatically (pass `--transport serial` or
`--transport hid` to force one):

```bash
# Watch raw bytes the device sends back (useful for status/firmware queries)
uv run python tools/sniffer.py --vid 0x0483 --pid 0x5750

# Send experimental probe sequences and log responses
uv run python tools/sniffer.py --vid 0x0483 --pid 0x5750 --init

# Send a custom raw payload
uv run python tools/sniffer.py --vid 0x0483 --pid 0x5750 --send "10 00 00 00"

# For a CDC-ACM unit like this one, e.g.:
uv run python tools/sniffer.py --vid 0x33c3 --pid 0x7788 --transport serial --send "10 00 00 00"
```

Log everything to a file for comparison:
```bash
uv run python tools/sniffer.py --vid 0x0483 --pid 0x5750 --log capture1.log
```

`probe.py` and `TEST.PY` in the repo root were earlier ad hoc scratch
scripts for poking `/dev/ttyACM0` directly — `tools/sniffer.py --transport
serial` now covers the same ground with logging/hexdump built in, so
prefer it going forward.

### What you're looking for

- **LCD protocol**: usually either (a) a header packet announcing frame
  size, followed by raw pixel chunks (RGB565 is most common for small
  cooler LCDs), or (b) a single compressed/JPEG blob transfer. Look for a
  repeating chunk pattern of ~60 bytes with a small incrementing sequence
  number in the header.
- **RGB protocol**: typically a single short packet:
  `[command_byte, zone, mode, R, G, B, speed, brightness, padding...]`.
  Change one color channel at a time in the official app and diff the
  captured bytes.

---

## Step 3 — Wire up the real protocol

Once you know the byte layout, there are exactly 3 places to edit:

**`core/lcd.py`** — `LCDController`:
- `_send_frame_start()` — the header packet announcing a new frame
- `_send_chunk()` — per-chunk pixel data header
- `_send_frame_end()` — commit/flush command

Also check `image_to_rgb565()` / `image_to_bgr565()` in the same file —
pick whichever matches your device's color order (you'll know because
red and blue will be swapped in the preview if it's wrong).

**`core/rgb.py`** — `RGBController._build_packet()`:
- Update byte offsets for command, zone, mode, RGB, speed, brightness
- Update `MODE_CODES` dict with the real mode byte values

Everything else — image loading, GIF playback threading, the GUI, color
pickers, presets — already works and doesn't need changes.

---

## Running the GUI

```bash
uv run lovingcool-control        # or: uv run python -m lovingcool_control
```

1. **Device tab**: click Connect once your device appears in "Recognized Devices"
2. **LCD Display tab**: pick an image/GIF/video or enter text, hit "Send to LCD" — the simple, single-shot path
   (video via `opencv-python-headless`, streamed frame by frame like GIF playback through
   `LCDController.play_frames()`; see `core/lcd.py`). Only one thing plays at a time: starting a GIF,
   video or live scene anywhere replaces whatever was playing. Has its own Landscape/Portrait toggle,
   same underlying `logical_canvas_size()`/`orient_for_device()` as Screen Designer (see below) —
   orientation isn't a Screen-Designer-only feature, both editing screens share it from `core/lcd.py`.
3. **Screen Designer tab**: build a live, multi-layer screen (see below)
4. **RGB Lighting tab**: pick a color/mode, hit "Apply"

---

## Screen Designer

A customizable live "dashboard" screen, separate from the simple LCD
Display tab — layers a background with hardware-stat overlays and streams
the composited result to the LCD continuously (1-30 fps).

**Canvas size is automatic**: both this page and the simple LCD Display
tab read the connected device's actual resolution (`DeviceSpec.lcd_width` /
`lcd_height`, e.g. 480x320 for the confirmed unit) rather than a hardcoded
guess. A label under the preview shows the current size and whether it
came from the device or a fallback default (shown before you've connected).

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

**Save/Load Preset**: the whole scene (background, fit mode, colors,
rotation, fps, and every widget) serializes to a small JSON file via
"Save Preset..." / "Load Preset...", so you don't have to rebuild a
dashboard from scratch each session.

**Start Live** begins a background thread (`core.canvas.ScenePlayer`) that
re-renders the scene (fresh sensor values, next background frame, current
widget positions) and sends it to the device on a timer — the GUI thread
never blocks on JPEG encoding or serial I/O, matching the existing
GIF-playback pattern in `core/lcd.py`. Dragging a widget while live is
running takes effect on the very next frame, since the renderer reads the
same widget list by reference.

Verified against real hardware: hardware-stat widgets (CPU %, disk %,
uptime, network), the CPU graph sparkline, a Contain-fit solid-color
background, a JSON preset round-trip (save → reload → identical scene),
and **both Landscape and Portrait orientations** were all run live for
several seconds each with no errors — confirmed the device always receives
an exact native-resolution (e.g. 480x320) frame regardless of orientation,
and that the logical preview frame is the swapped aspect (320x480) in
Portrait, matching what compose_scene() is supposed to produce. The
drag-to-move interaction itself was verified with a simulated click-drag
(`QTest.mousePress`/`mouseMove`/`mouseRelease`) in **both orientations**,
confirming the on-screen pixel position maps back to the exact expected
canvas coordinate whether the canvas is wide or tall.

---

## Packaging for distribution

Each platform's binary must be built **on that platform** — PyInstaller
doesn't cross-compile. Two build scripts are provided that handle venv
setup, dependency install, and the PyInstaller invocation:

```bash
# Linux — produces dist/lovingcool-control (+ dist/udev/99-lovicool.rules alongside it)
./build_linux.sh
```

```bat
:: Windows — produces dist\lovingcool-control.exe
build_windows.bat
```

There's no macOS script (not tested against real hardware on macOS), but
the same one-liner from earlier PyInstaller usage still applies there if
needed: `uv run pyinstaller --onefile --windowed --name lovingcool-control --paths src packaging/pyinstaller_entry.py`.

**Verified on this machine:** `build_linux.sh` was run end-to-end and the
resulting `dist/lovingcool-control` binary launches and enumerates the AIO
correctly — PyInstaller's binary dependency analysis picks up hidapi's
bundled `.so` files (under `hidapi.libs/`, referenced via RPATH) automatically;
no extra `--collect-binaries` hook was needed. `build_windows.bat` is
untested (no Windows machine available here) but follows the same
structure — if `hid`/`hidapi`'s DLL doesn't get bundled automatically on
Windows, add `--collect-all hid` to the `pyinstaller` call in the script.

---

## Running as a service (auto-start, auto-restart, one-command stop)

Separate install scripts per platform under `packaging/`, both giving the
same behavior: lovingcool-control auto-starts, and if it ever exits — window
closed, crash, anything — it's automatically relaunched a few seconds
later ("loop run when done"), continuing until you deliberately stop it.

**Linux — systemd `--user` service** (`packaging/linux/`):

```bash
./build_linux.sh
packaging/linux/install_service.sh
```

This is a **user** service on purpose, not a system one — it's a GUI app
that needs the logged-in session's `DISPLAY`/`WAYLAND_DISPLAY`, which a
root-level system service doesn't have access to. Run the installer from
an actual graphical login session (not bare SSH) so it can capture that
environment. `Restart=always` in the unit file is the auto-restart loop;
`StartLimitBurst=10` over 60s keeps a persistently-broken binary from
crash-looping forever.

```bash
packaging/linux/stop_service.sh        # stop WITHOUT triggering auto-restart
packaging/linux/uninstall_service.sh   # remove the service entirely
journalctl --user -u lovingcool-control -f      # logs
```

**Verified live on this machine**: installed the service, confirmed the
process was genuinely running (`systemctl --user status`, real memory
usage), `kill -9`'d it and confirmed systemd relaunched it with a new PID
within seconds, confirmed `stop_service.sh` leaves it stopped (no
respawn), then uninstalled and removed the test binary to leave the
machine exactly as it was.

**Windows — Task Scheduler + restart-loop wrapper** (`packaging/windows/`):

```bat
build_windows.bat
packaging\windows\install_service.bat
```

**Why this isn't a literal Windows Service:** since Windows Vista, SCM
services run in Session 0 and cannot show UI on the interactive desktop —
a GUI app registered as a real Windows Service would just never appear on
screen. The correct equivalent for "auto-start and auto-restart a GUI
app" is a **Scheduled Task** that runs at logon in the user's own session,
pointed at a small wrapper (`lovingcool-control-loop.bat`) that reruns the exe
whenever it exits, launched via a hidden VBScript (`lovingcool-control-loop-hidden.vbs`)
so no stray console window is left sitting around.

```bat
packaging\windows\stop_service.bat        REM stop WITHOUT auto-restart
packaging\windows\uninstall_service.bat   REM remove the scheduled task
schtasks /run /tn "lovingcool-control"             REM start it right now
```

This is logic-reviewed (the `/tr` nested-quote escaping for `schtasks`,
the `.stop`-sentinel handshake between `stop_service.bat` and the loop,
the `wscript.exe //B` hidden-launch syntax) but **not run on a real
Windows machine** — no Windows box was available in this environment. If
`install_service.bat` fails to register the task, try running it as
Administrator; if `hid`/`hidapi` doesn't load once running as a scheduled
task, see the note in the Packaging section above.

---

## Contributing protocol findings

If you reverse-engineer your model, consider documenting the VID:PID and
byte layout you find — other Lovi.Cool owners on Linux have the exact same
problem you started with, and a shared protocol doc would help the wider
community the way `liquidctl`'s device list does.
