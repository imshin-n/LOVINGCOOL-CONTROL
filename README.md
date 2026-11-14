# LOVINGCOOL-CONTROL

Open-source control for **Lovingcool / Lovi.Cool AIO liquid coolers with a
screen**, built because the vendor only ships Windows software. Show
images, GIFs, videos, text or a live dashboard of system stats on the
cooler's display, from a GUI, the command line or your own scripts.

Linux first (a background service that starts at login, keeps the screen
lit and comes back after a replug or reboot); the GUI also runs on
Windows and macOS.

> **Origins:** this started as a fork of
> [newan0805/loving_cool_aio_display_manage](https://github.com/newan0805/loving_cool_aio_display_manage)
> ("lovi-ctrl"), whose author first worked out the screen protocol
> ([docs/PROTOCOL.md](docs/PROTOCOL.md)). The code has since been rewritten
> from scratch as a separate project.

## Supported coolers

| USB ID | Screen | Status |
|---|---|---|
| `33c3:7791` | Lovingcool SC60, 4" 480×480 (`TXW818-ST7701S-4.0inch`, firmware 3.1) | Works, verified |
| `33c3:7788` | Lovi.Cool 7" 480×320 (`txm818-7inch`, firmware Ver1.0) | Works (verified by the original project) |
| `33c3:7797` | Sibling build in the vendor's whitelist | Untested, probably works |

Not sure which you have? `lsusb | grep -i 33c3`. Another model can often be
added with a few lines of config (see [Adding a cooler](#adding-a-cooler)).
RGB/fan lighting is driven by a separate USB device on this product line
and isn't supported yet.

## Features

- **Show anything**: images, GIFs and videos (any length or resolution;
  decoded on the fly), or a line of text in any colour and size.
- **Screen Designer**: build a live dashboard by dragging widgets onto a
  background (colour, image, GIF or video): clock, date, CPU/GPU/RAM/disk
  usage and temperatures, network, battery, uptime, custom text and a CPU
  history graph. Landscape or portrait, any rotation. See
  [docs/SCREEN_DESIGNER.md](docs/SCREEN_DESIGNER.md).
- **Keeps running**: the `lovingcool-controld` service needs no desktop,
  waits for the cooler, reconnects after an unplug, and restores what you
  last showed and the brightness.
- **Scriptable**: `lovingcool-ctl` and a local JSON socket, for keybinds,
  bar widgets or cron jobs.
- Brightness control. Light on resources: the service uses about 42 MB of
  RAM and under 1% of one CPU core while running a live stats screen.

## Install

### Arch Linux (and derivatives)

```bash
git clone https://github.com/imshin-n/LOVINGCOOL-CONTROL.git
cd LOVINGCOOL-CONTROL/packaging/arch
makepkg -si
systemctl --user enable --now lovingcool-control
```

Unplug and replug the cooler once so the udev rule applies. The package
installs the app, `lovingcool-ctl`, the systemd user service, the udev rule
and a launcher entry.

### Other Linux distributions

Needs [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/imshin-n/LOVINGCOOL-CONTROL.git
cd LOVINGCOOL-CONTROL
sudo install -Dm644 packaging/linux/70-lovingcool-control.rules /etc/udev/rules.d/70-lovingcool-control.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
packaging/linux/install_service.sh
```

`install_service.sh` installs the commands into `~/.local/bin` (with
`uv tool install`) and enables the systemd user service. Re-run it after
`git pull` to upgrade; `packaging/linux/uninstall_service.sh` removes it.
If the original project's `lovi-ctrl` service is installed, the script
disables it (add `--remove-old` to also delete its files).

### Windows / macOS / trying it out

```bash
uv run lovingcool-control
```

Without the background service, the GUI runs the same service inside
itself, so the screen stops when you close the window. For standalone
binaries and the Windows auto-start task see
[docs/BUILDING.md](docs/BUILDING.md); both are untested on real Windows
hardware.

## Using it

### The app

Open **LOVINGCOOL-CONTROL** from your launcher (or run `lovingcool-control`).
It uses plain Qt widgets in a light or dark theme (Settings → Theme, or
follow the system), laid out like a desktop RGB-controller app:

- **Devices**: your cooler as a tab on the left. A preview of the screen,
  a **Mode** list (Image / GIF / Video, Text, Scene, Off) with that mode's
  options, orientation, rotation, brightness, and **Apply**.
- **Screen Designer**: build a dashboard and press **Start Live**. Edits
  you make while live appear on the screen within about half a second.
- **Information**: device details, the service, other USB devices that
  might be an unrecognised cooler, and the event log.
- **Settings**: theme, and where your settings and scenes live.
- The bottom bar has your saved scenes (**Show Scene**, **Save Scene** from
  the designer), **Screen Off** and **Rescan Devices**.

The app is a client of the background service, so whatever you show keeps
running after you close it.

### The command line

```bash
lovingcool-ctl status
lovingcool-ctl show ~/Pictures/cat.gif                 # image, GIF or video; loops
lovingcool-ctl show clip.mp4 --once --portrait
lovingcool-ctl text "Hello" --fg ffcc00 --bg 101020 --size 64
lovingcool-ctl scene ~/.config/lovingcool-control/scenes/stats.json
lovingcool-ctl brightness 60
lovingcool-ctl off
lovingcool-ctl --json status                            # machine-readable
```

Everything is remembered: after a reboot or replug the screen comes back
showing the same thing.

### Scripting

`lovingcool-ctl` talks to the service over a Unix socket only your user can
open, `$XDG_RUNTIME_DIR/lovingcool-control.sock`, using one JSON object per
line. Anything that can write to a socket can drive the screen:

```bash
echo '{"cmd": "text", "text": "Build passed", "fg": [80, 220, 120]}' |
  socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/lovingcool-control.sock
```

| Request | Fields |
|---|---|
| `status` | Reply: `connected`, `device` (name, usb_id, model, firmware, panel), `content`, `playing`, `brightness`, `error` |
| `show` | `path`; optional `loop`, `rotation`, `orientation` (`landscape`/`portrait`) |
| `text` | `text`; optional `fg`, `bg` (`[r, g, b]`), `font_size`, `orientation` |
| `scene` | `path` to a Screen Designer `.json` |
| `brightness` | `value` 0-100 |
| `off`, `reconnect`, `ping` | none |

Every reply has `"ok": true` or `"ok": false, "error": "..."`.

## Files

| Path | What |
|---|---|
| `~/.config/lovingcool-control/state.json` | What's showing and the brightness (restored on start) |
| `~/.config/lovingcool-control/scenes/` | Screen Designer scenes, autosave and the live scene |
| `~/.config/lovingcool-control/devices.toml` | Your own device entries (optional) |
| `~/.config/lovingcool-control/lovingcool-control.conf` | GUI settings |

On Windows these live under `%APPDATA%\lovingcool-control\`, on macOS under
`~/Library/Application Support/lovingcool-control/`.

## Troubleshooting

**The screen is dark.** These panels show nothing unless something is
streaming to them. Check `lovingcool-ctl status`: if the service isn't
running, `systemctl --user start lovingcool-control`; if it says
"showing: off" or nothing, show something.

**"Permission denied" on `/dev/ttyACM0`.** Install the udev rule (above)
and replug the cooler. Quick workaround: add yourself to the group that
owns the port (`ls -l /dev/ttyACM0`; `uucp` on Arch, `dialout` on
Debian/Ubuntu) and log in again.

**"Could not exclusively lock port".** Another program has the screen
open, usually an old `lovi-ctrl`, a second service, or the vendor app under
Wine. Close it; `fuser /dev/ttyACM0` shows who.

**Logs:** `journalctl --user -u lovingcool-control -f`.

**Check the device directly** (stop the service first so the port is free):

```bash
systemctl --user stop lovingcool-control
uv run python tools/check_device.py --frames 20
systemctl --user start lovingcool-control
```

## Adding a cooler

1. Find its USB ID: `uv run python tools/enumerate.py` (or `--watch` and
   replug). The app's Status page also lists candidates.
2. Add it to `~/.config/lovingcool-control/devices.toml`:

   ```toml
   [[device]]
   name = "My cooler"
   vid = 0x33c3
   pid = 0x7799
   transport = "serial"
   baudrate = 2_000_000
   dtr = true
   lcd_width = 480
   lcd_height = 480
   lcd_color_format = "JPEG"
   keepalive_ms = 200     # if a still image only flashes
   ```

   Every field is documented in
   [`core/devices.toml`](src/lovingcool_control/core/devices.toml); an
   entry with the same USB ID overrides the built-in one.
3. Stop the service and run `uv run python tools/check_device.py --frames 20`.
4. If it works, please open an issue or PR with the USB ID and the `info`
   line it printed. If it doesn't, [docs/PROTOCOL.md](docs/PROTOCOL.md)
   explains the protocol and how to investigate a new one.

## Development

```bash
uv sync                  # environment with dev tools
uv run pytest            # ~85 tests, no hardware needed (GUI tests run headless)
uv run ruff check src tests tools
uv run lovingcool-control
```

```
src/lovingcool_control/
├── core/       protocol.py (frame codec), device.py (connection, devices.toml),
│               lcd.py (encoding, playback, keep-alive), canvas.py (scenes), sensors.py
├── service/    daemon.py (lovingcool-controld), server.py (socket), state.py
├── gui/        app.py, bridge.py (GUI -> service), theme.py, pages/ (device_view, info,
│               settings, designer/), widgets/
└── cli.py      lovingcool-ctl
tools/          enumerate.py (find devices), sniffer.py (raw bytes / commands), check_device.py
packaging/      linux/ (systemd unit, udev rule, desktop entry), arch/ (PKGBUILD), windows/
docs/           PROTOCOL.md, SCREEN_DESIGNER.md, BUILDING.md
```

## License

MIT, see [LICENSE](LICENSE). Thanks to the author of the original
[lovi-ctrl](https://github.com/newan0805/loving_cool_aio_display_manage) for
working out the screen protocol this builds on.
