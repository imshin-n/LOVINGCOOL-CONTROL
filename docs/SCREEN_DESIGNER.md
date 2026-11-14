# Screen Designer

Build a live screen out of a background and widgets, then run it on the
cooler.

## Background

Choose **Colour**, **Image**, **GIF** or **Video**. For files, **Fit** sets
how the picture meets the screen:

- **Fill (crop):** fills the screen, cropping what overflows.
- **Fit (letterbox):** shows all of it, with bars in the fill colour.
- **Stretch:** fills exactly, distorting if the shape differs.
- **Original size:** no scaling, centred.

Transparent areas (PNG, GIF) show the **fill colour**, which also fills the
bars and the corners left by rotation. Videos are decoded as they play, so
long or high-resolution files are fine.

## Layout

**Orientation** switches between landscape and portrait. In portrait you
design on a tall canvas and the finished frame is turned to fit the panel;
widgets keep their relative positions when you switch. **Rotation** adds any
further angle on top, for example 180° if the cooler is mounted upside down.

## Widgets

- **System stat:** time, date, CPU load / temperature / clock speed, memory,
  disk space, GPU load / temperature (AMD on Linux, NVIDIA via `nvidia-smi`),
  battery, network totals, uptime. Optionally prefixed with its name
  ("CPU: 42%").
- **Text:** any fixed text.
- **CPU graph:** the last minute of CPU load as a line graph.

Add, edit (or double-click), duplicate and remove widgets from the list.
On the canvas, click a widget to select it, drag to move it, and press
Delete to remove it. **Snap** keeps drags on a 10 px grid. Each widget has
its own position, size and colour.

Stats refresh once a second, and only the stats the scene shows are read,
so even busy scenes cost very little CPU. Unavailable stats show "N/A".

## Running it

**Start Live** sends the scene to the service, which keeps running it after
you close the window (with the background service installed). Changes you
make while live reach the screen within about half a second. **Stop Live**
turns the screen off. The frame rate is adjustable; for a stats screen
1–2 fps is plenty, since unchanged frames aren't re-sent anyway.

## Scene files

**Save Scene As…** / **Open Scene…** store scenes as JSON, by default in
`~/.config/lovingcool-control/scenes/`. Saved scenes appear in the bottom
bar's Scene list and the Devices tab's Scene mode, and can be run without
the GUI:

```bash
lovingcool-ctl scene ~/.config/lovingcool-control/scenes/stats.json
```

Your work in progress is saved automatically and reopened next time.
