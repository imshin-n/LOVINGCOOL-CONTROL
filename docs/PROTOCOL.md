# Screen protocol reference

How the Lovingcool / Lovi.Cool cooler screens (sold under several brands,
firmware from Dongguan Hongtai Technology, "LOVINGCOOL MONITOR") are driven
over USB, as implemented in `core/protocol.py`, `core/device.py` and
`core/lcd.py`.

**Credit:** the protocol was first worked out by the author of
[newan0805/loving_cool_aio_display_manage](https://github.com/newan0805/loving_cool_aio_display_manage),
who recovered it from the vendor's Windows app (an Electron app, so its
JavaScript source can be unpacked from the installer). The SC60 behaviour
below was established on real hardware for this project.

## Transport

The screen controller is a **USB CDC-ACM serial port**, not HID
(`/dev/ttyACM*` on Linux, a COM port on Windows). It enumerates as
"RT-Thread" / "RTT Virtual Serial" or "MONITOR / HONGTAI", vendor ID
`0x33c3`.

| | |
|---|---|
| Baud rate | 2,000,000 (nominal; it's USB, but set it anyway) |
| DTR | Raised, with DSR/DTR flow control. Required on the 7788; the SC60 works either way |
| Port | Open it exclusively: two writers interleave frames and corrupt the picture |

RGB/fan lighting, where present, is a **separate** USB device (often a CH340
USB-serial bridge) with its own protocol. It isn't supported.

## Command channel

Requests and replies share one frame layout, all integers little-endian:

```
offset  size  field
0       2     sync      0x55 0xAA
2       2     length    total frame length = payload length + 7
4       1     key       command
5       n     payload
5+n     2     checksum  sum of bytes 0 .. 4+n, modulo 65536
```

Example, getInfo with no payload: `55 aa 07 00 06 0c 01`.

Replies carry UTF-8 JSON as the payload, e.g. from an SC60:

```json
{"cmd": "info", "data": {"width": 480, "height": 480, "brightness": 80,
 "model": "TXW818-ST7701S-4.0inch", "version": "3.1", "angle": 270,
 "uid": "…", "shape": "0BB1", "i_path": "", "diplay_on": true}}
```

| Key | Meaning | Payload | Reply |
|---|---|---|---|
| `0x01` | restart the controller | none | none |
| `0x03` | backlight brightness | 1 byte, 0–100 | none |
| `0x06` | get device info | none | JSON as above |
| `0x11` | start live streaming | none | none |
| `0x21` | close | none | none (firmware ≥ 3.1) |

The vendor app also has keys for idle/screen-off timeouts (`0x14`,
`0x15`, `0x26`), a serial number (`0x23`) and a motor (`0x25`); they aren't
used here.

**Reading replies reliably:** at 2 Mbaud, reading the 5-byte header and then
exactly the advertised remainder as two separate reads straight after the
write lost data on real hardware. What works is pausing ~150 ms and then
reading whatever has arrived in bulk, feeding it to a streaming parser that
resynchronises on `55 AA` and checks lengths and checksums
(`protocol.FrameParser`). Also, pyserial reconfigures the port every time
its `timeout` is assigned, so don't do that inside a read loop.

## Image channel

Pictures don't use the command framing. To show frames:

1. Send command `0x11` once to start streaming.
2. For each frame, scale it to the panel's native resolution (from getInfo),
   encode it as **JPEG** under a size budget (~50 KB; this app searches for
   the highest quality that fits), and write it to the port:
   - firmware **≤ 2.8**: the JPEG bytes as they are;
   - firmware **> 2.8**: `[length: 4 bytes LE][jpeg][checksum: 2 bytes LE]`,
     the checksum being the byte sum of length + JPEG, modulo 65536.
3. To leave streaming mode, write `FF D9 FF D9` (two JPEG end markers).

The version string is parsed for its number (`"Ver1.0"` → 1.0, `"3.1"` →
3.1) to choose the framing.

## Per-model notes

**33c3:7788, 7" 480×320 (`txm818-7inch`, firmware Ver1.0):** bare JPEG frames.
Needs DTR flow control.

**33c3:7791, SC60 4" 480×480 (`TXW818-ST7701S-4.0inch`, firmware 3.1),
verified 2026-11-14:**

- Uses the length + checksum frame wrapper.
- **Blanks about 0.5 s after the last frame**, whether or not streaming was
  ended. A single still image only flashes, so the last frame has to be
  re-sent continuously; this app re-sends it every 200 ms while idle
  (`keepalive_ms` in `devices.toml`).
- **Ignores commands while streaming.** End the stream (`FF D9 FF D9`), send
  the command, then `0x11` and the last frame again. Because of the 0.5 s
  grace period this doesn't flicker.
- Reports `angle: 270`, but frames composed upright display upright.
- Has no stored image (`i_path` is empty): the screen is dark whenever
  nothing streams to it.

**33c3:7797:** listed by the vendor app for a sibling brand; untested.

## Investigating an unknown model

1. `uv run python tools/enumerate.py --watch` and replug the cooler to find
   its VID:PID and whether it's serial or HID.
2. If it's serial, add it to `devices.toml` and try
   `uv run python tools/check_device.py --frames 20` (stop the service
   first). Same vendor firmware usually means the same protocol.
3. Otherwise look at what the vendor's software sends:
   - unpack its source if it's an Electron app (installer → `app.asar`,
     e.g. with `7z` and `npx asar extract`), and search for the serial
     setup and byte arrays; or
   - capture USB traffic (Wireshark with usbmon on Linux or USBPcap on
     Windows) while changing one thing at a time in the vendor app.
4. `uv run python tools/sniffer.py VID:PID --command 06` or `--raw HEX`
   sends test bytes and shows what comes back.

Please open an issue with what you find, even partial results.
