# Building and other platforms

For Arch Linux use the PKGBUILD, and on other Linux distributions use
`packaging/linux/install_service.sh` (see the README). This page covers
standalone binaries and Windows.

## Standalone binaries (PyInstaller)

Each platform's binary must be built **on that platform** — PyInstaller
doesn't cross-compile. Two build scripts are provided that handle venv
setup, dependency install, and the PyInstaller invocation:

```bash
# Linux — produces dist/lovingcool-control (+ dist/udev/70-lovingcool-control.rules alongside it)
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

## Windows

On Windows the GUI runs the service inside itself (there is no separate
background service yet), so the screen runs while the app does. To keep it
running, the scripts below start the app at logon and restart it if it exits.

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
