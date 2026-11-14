# LCD protocol notes

What's known about the Lovingcool / Lovi.Cool ("LOVINGCOOL MONITOR",
Dongguan Hongtai Technology) cooler screens, and how it was worked out.
Most of this was reverse-engineered by the author of the original
[lovi-ctrl](https://github.com/newan0805/loving_cool_aio_display_manage)
project from the vendor's own Windows app; the SC60 section and the
code references were added here.

## Status

For the unit this project was developed against, the LCD protocol is no
longer a placeholder — it's implemented in `core/device.py` (command
channel) and `core/lcd.py` (image streaming), and confirmed both by
querying the live device and by displaying an image on the physical
screen through this app's own GUI.

---

## How it was found, and how it works

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
| DTR | asserted (`dtr: true`) | Needed on the 7788 (without it reads stay silent / bytes drop). The SC60 (7791) works either way; it's on to match the vendor app. |
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

`core/lcd.py`'s `LCDController` follows this: `fit_jpeg()` does the quality
search (a binary search seeded with the previous frame's quality, rather than
stepping down by 2), `_build_jpeg_frame()` adds the length+checksum wrapper
when the firmware version is above 2.8, and `send_raw_jpeg_frame()` sends the
one-time `0x11` before the first frame.

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
  trusting the header's length field to drive a second precise read. `AIODevice._read_reply()` keeps that approach
  but feeds the bytes through the streaming `FrameParser` in
  `core/protocol.py`, which also survives truncated or noisy replies.
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

### 8. SC60 4" (33C3:7791, firmware 3.1): confirmed 2026-11-14

`getInfo` reply on a real unit:

```json
{"uid":"89777A17212F","width":480,"height":480,"diplay_on":true,"brightness":80,
 "i_blocks":4096,"i_block_size":2048,"i_block_free":318,"i_path":"",
 "model":"TXW818-ST7701S-4.0inch","version":"3.1","shape":"0BB1","region":"","angle":270}
```

- **Same command channel and image channel** as the 7788. Firmware `3.1`
  parses as > 2.8, so every JPEG goes out in the
  `[4-byte LE length][jpeg][2-byte LE checksum]` wrapper. Confirmed by
  streaming frames that display upright and clean.
- **DTR on or off both work** at 2,000,000 baud.
- **The panel blanks about 0.5 s after the last frame**, whether or not
  the stream is ended with the `FF D9 FF D9` marker. A single still image
  only flashes. The fix is a keep-alive: re-send the last frame at least
  every ~200 ms while idle (`keepalive_ms = 200` in `devices.toml`;
  `LCDController` handles it).
- **Commands are ignored while a JPEG stream is open.** End the stream
  (`FF D9 FF D9`), send the command, then re-send `0x11` and the last
  frame. Because the panel only blanks after ~0.5 s, this doesn't flicker
  (`LCDController.command()`, used for brightness and getInfo).
- `angle: 270` is reported but frames composed upright display upright;
  no extra rotation is needed.
- `i_path` is empty: the unit has no stored image to fall back on, so the
  screen is dark whenever nothing is streaming to it.

---

## Adding a device whose protocol is unknown

The usual community-driver workflow (the way `liquidctl` supports dozens of
devices). If your cooler comes from the same OEM, try the built-in protocol
first: add its VID:PID to `devices.toml` (see the README) and run
`uv run python tools/check_device.py`.

### 1. Find the VID:PID

```bash
uv run python tools/enumerate.py           # lists HID and USB-serial devices, likely ones highlighted
uv run python tools/enumerate.py --watch   # then unplug/replug the cooler
```

If it shows up under "USB-serial (CDC-ACM) devices", use `transport = "serial"`.

### 2. Get the vendor's protocol

Easiest if the vendor app is Electron (like this one): extract its source
as described in section 2 above. Otherwise:

#### Option A: USB packet capture

1. Install [Wireshark](https://www.wireshark.org/) with USBPcap (Windows) or
   usbmon (Linux, built into the kernel).
2. Run the official Lovi.Cool/manufacturer software in a Windows VM (or dual
   boot) with the capture running.
3. Make **one change at a time** — set LCD to a solid red image, then static
   RGB red, then breathing mode — and label each capture.
4. Compare byte sequences between captures to isolate which bytes control
   color, mode, image data, etc. (this is the standard technique documented
   in community AIO/RGB reverse-engineering guides).

#### Option B: this project's sniffer

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

### 3. What you're looking for

- **LCD protocol**: usually either (a) a header packet announcing frame
  size, followed by raw pixel chunks (RGB565 is most common for small
  cooler LCDs), or (b) a single compressed/JPEG blob transfer. Look for a
  repeating chunk pattern of ~60 bytes with a small incrementing sequence
  number in the header.
- **RGB protocol**: typically a single short packet:
  `[command_byte, zone, mode, R, G, B, speed, brightness, padding...]`.
  Change one color channel at a time in the official app and diff the
  captured bytes.

### 4. Wire it up

Serial devices with the same JSON command channel and JPEG frames need only
a `devices.toml` entry. Anything else needs a transport or framing in
`core/device.py` / `core/lcd.py`: `LCDController.send_raw_frame()` is a
starting point for chunked raw-pixel (RGB565/BGR565) protocols, and
`core/rgb.py` is a stub for a separate RGB controller.

---

## Contributing protocol findings

If you reverse-engineer your model, consider documenting the VID:PID and
byte layout you find — other Lovi.Cool owners on Linux have the exact same
problem you started with, and a shared protocol doc would help the wider
community the way `liquidctl`'s device list does.
