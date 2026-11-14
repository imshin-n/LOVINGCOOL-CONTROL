# Building and other platforms

On Linux, use the Arch PKGBUILD or `packaging/linux/install_service.sh`
(see the README). This page covers standalone binaries and Windows.

## Standalone binaries (PyInstaller)

Build on the platform you're targeting; PyInstaller doesn't cross-compile.
Both scripts need [uv](https://docs.astral.sh/uv/).

```bash
./build_linux.sh          # -> dist/lovingcool-control, plus dist/udev/ with the udev rule
```

```bat
build_windows.bat         :: -> dist\lovingcool-control.exe
```

The binary is the GUI. It runs the screen service inside itself, so the
screen keeps going only while the app is open. On Linux, prefer the real
background service, which needs no window and starts at login.

macOS has no script, but the same command works there:

```bash
uv run pyinstaller --onefile --windowed --name lovingcool-control \
    --paths src --collect-data lovingcool_control packaging/pyinstaller_entry.py
```

## Windows

Untested on real hardware so far.

1. Build the exe with `build_windows.bat` (or run from source with
   `uv run lovingcool-control`).
2. To start it when you log in:

   ```bat
   packaging\windows\autostart.bat install
   ```

   This creates a per-user scheduled task (no administrator rights needed).
   `autostart.bat remove` deletes it. Pass the exe's path as a second
   argument if it isn't in `dist\`.

The screen is a USB serial (COM) device, so no driver should be needed. If
a SmartScreen warning appears, it's because the exe isn't code-signed.
