# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation
- Synced the docs with 1.3.0–1.3.1, which had shipped without them: README (1.3.1
  highlight, full test list, `version.py` in Architecture, Python 3.11 + venv, prebuilt
  EXE on the Releases page, when the `volume` LED lights), QUICKSTART (WinUSB driver,
  build command, LED and acceleration notes, status text), `install.bat` (WinUSB; the
  `>` in the Zadig hint was redirecting output to a file instead of printing it).

### Fixed
- Shortcut recording: NumLock no longer records as `alt`, and Shift+digit/symbol records the base key (`shift+1`, not `shift+exclam`) so `keyboard` can replay it (#18). (`ui/action_editor.py`)

## [1.3.1] — 2026-10-28

### Added
- **EXE file properties.** `PowerMate.exe` now shows a product name ("Griffin PowerMate
  Controller") and file/product version in Explorer → Properties → Details. The values
  come from `version.py` (single source; bump it before tagging), and `build.spec`
  generates the Windows version resource on every build. (`version.py`, `build.spec`)

## [1.3.0] — 2026-10-27

### Fixed
- **Knob scrolling is immediate and stops when you stop.** Scrolling now injects the
  wheel event directly (`user32.mouse_event`) instead of `pyautogui.scroll`, which sent
  1/40 of a notch and slept 0.1 s per call so turns piled up. `amount` is in lines
  (3 lines = one notch = wheel delta 120); fractional lines carry over. (`actions.py`)
- `timing.rotate_sensitivity` and `timing.rotate_acceleration` now actually apply, at
  startup and when settings change. (`main.py`)
- `config.json`, `powermate.log` and the tray icon are found next to the app instead of
  in the current directory, so **Start with Windows** (CWD = `System32`) no longer fails
  to open the log; if the app folder isn't writable they fall back to
  `%LOCALAPPDATA%\PowerMate`. `python main.py debug` now enables DEBUG logging.
  (`paths.py`, `main.py`, `config.py`, `tray.py`)
- `requirements.txt` pinned `pycaw==20240413`, which does not exist on PyPI, so
  `pip install -r requirements.txt` failed. Pinned to `pycaw==20251023`, the version
  `audio.py` was adapted to in 1.1.0.
- **Unplugging and re-plugging the knob reconnects without restarting the app.** Read
  errors other than timeouts now reach the worker (close → search again), a failed
  `claim_interface` no longer reports "connected", and LED writes racing a disconnect
  no longer kill the LED thread. If the device is present but its read pipe keeps
  failing, re-searching backs off (0 s, 1 s, 2 s, 4 s, then 5 s) instead of looping
  every 10 ms. (`device.py`, `main.py`)
- Double/triple press with slightly longer clicks no longer splits into single presses: the multi‑click window is now measured from release, not press. (`gesture.py`)
- **A bad setting no longer breaks the app.** Invalid or out-of-range values in
  `config.json` (e.g. `"mode": "rainbow"`, `"fade_speed": 0`, flash on/off both `0`,
  `"rotate_acceleration": null`, a non-numeric `amount`) are replaced by their defaults
  in memory and logged as warnings; loading never rewrites `config.json` (the next save
  from the settings window writes the replacements). Before, these
  stopped the app from starting or killed the LED thread. An error in one LED update is
  now logged and the loop continues. (`config.py`, `led.py`, `main.py`, `gesture.py`)
- **Save no longer fails silently on a bad field.** A non-numeric timing/LED entry is
  skipped (its stored value kept), the other fields are saved, and the rejected fields
  are listed in orange in the status bar. (`ui/timing_panel.py`, `ui/led_panel.py`,
  `ui/app_window.py`)
- **A shortcut like `1` sends the key 1.** The action editor converted every numeric
  field to a number, so `keys: "1"` was saved as `1` — scan code 1, i.e. **Esc**. Only
  `amount` is numeric now; keys, paths and commands stay text, and a non-numeric
  amount falls back to the action's default. (`ui/action_editor.py`, `config.py`)

### Added
- **Rotation acceleration curve.** With `rotate_acceleration` > 0, slow turns stay 1:1
  and fast turns are multiplied (up to 1 + 9×value); a pause over 0.3 s resets to
  precise mode. Default 0.0 keeps the previous behaviour. (`gesture.py`)

### Removed
- `pyautogui` dependency.

## [1.2.0] — 2026-06-07

### Added
- **Translucent on-screen display (OSD).** A small two-line overlay now pops up in the
  bottom-right corner (over the taskbar) on every knob gesture: line 1 is the gesture
  (*Single / Double / Triple / Long Press, Knob Left / Right, Press + Knob Left / Right*),
  line 2 is the action that gesture is bound to in the **active** profile (e.g. *Volume
  Up*, *Play / Pause*, or the key combo for `key` / `macro`). Unbound gestures still show
  with `—` on line 2. The overlay is always-on-top, never steals focus, and stays out of
  Alt-Tab / the taskbar. It auto-hides ~1.2s after the last event with a short fade, and
  the timer resets while you keep turning. Works even when the main window is minimized to
  the tray. (`ui/osd.py`)
- **OSD settings tab** with full styling control: enable toggle, opacity, duration,
  background color (hex + color picker), font family (from installed system fonts), font
  color (hex + picker), and font size (8–24pt), plus a **Preview** button. All settings
  persist under a new `osd` block in `config.json` and apply live. (`ui/osd_panel.py`)

## [1.1.1] — 2026-06-06

### Security
- **High-level vulnerability assessment performed** (see `README.md` →
  *Security Considerations*). Summary: no network listeners or remote attack surface;
  the app runs with normal user privileges; the only code-execution paths (`run`,
  `launch`, `macro`, `key`) are driven by the user's own local config by design.
  Documented the threat model and the `run` (shell) caveat.

### Fixed
- **Config shared-state aliasing:** `DEFAULT_CONFIG.copy()` was a shallow copy, so a
  fresh install that edited any nested setting (a profile, LED, timing) would mutate the
  module-level defaults. Now uses `copy.deepcopy`.
- **Config reload spam:** a malformed `config.json` left `_last_mtime` unset, causing the
  watcher to re-read the broken file every second (log/CPU churn). The mtime is now
  recorded even on parse error, so it only retries when the file actually changes.

### Changed / Performance
- **Knob Activity panel** no longer queues Tk callbacks while the window is hidden in the
  tray (where the app spends most of its life), reducing idle overhead during fast
  rotation.
- Removed an unused import (`pathlib.Path`) from `config.py`.

### Notes
- Cursory memory-leak review: GUI widgets are destroyed before being rebuilt (profile
  list, action-editor params, dialogs); USB resources are released via
  `release_interface` + `dispose_resources`; LED/worker/watch threads are bounded loops;
  the debug textbox is capped at 300 lines; per-thread COM interfaces are cached on
  long-lived threads. No unbounded growth found.

## [1.1.0] — 2026-06-06

### Fixed
- **Audio control restored.** pycaw 20251023 removed `AudioDevice.Activate()`, which the
  volume/mute/mic code relied on; every call failed silently and `get_volume` returned a
  fake `0.5`. Rewrote `audio.py` to use the low-level `MMDeviceEnumerator`, which works
  across pycaw versions. Verified setting/reading volume and toggling mute from both the
  worker and LED threads.
- **Volume/scroll math.** The rotation delta was used *as* the increment (`vol + 1` →
  instantly 100%/0%) instead of multiplying the configured step. A turn now steps smoothly
  (`delta × amount`).

### Added
- **3-step keyboard macro action** (`macro`): sends up to three key combos in sequence.
  The action editor gained a 3-field recorder (press the **●** button to capture each
  step).
- **Knob Activity / debug panel:** a live, timestamped log at the bottom of the window
  showing each incoming trigger, the focused app, and the action taken — wired
  thread-safely from the worker thread, capped and clearable.
- **Start with Windows** toggle: a checkbox that registers/unregisters the app in the
  per-user `HKCU\…\Run` key (`startup.py`).
- **Explicit window controls:** *Minimize to Tray* (keep running) and *Quit* buttons; the
  window's **X** continues to minimize to the tray.
- **`spotify.exe` profile** using Spotify's in-app shortcuts (`space`, `ctrl+right`,
  `ctrl+left`) to sidestep global media-key routing, so play/pause toggles reliably when
  Spotify is focused.
- `_test_features.py` covering the macro action, startup module, debug panel, and
  triple/long-press bindings.

### Packaging
- `build.spec` now bundles all `pycaw` submodules so the audio fix survives in the
  one-file EXE.

## [1.0.0] — 2026-06-06

### Added
- Initial release: a from-scratch Python (Windows 11) controller for the Griffin
  PowerMate USB knob.
- **Gesture engine:** rotate left/right, single/double/triple press, long press, and
  press-while-rotating, with configurable multi-click and long-press timing.
- **Per-application profiles** with inheritance from a `default` profile, using
  foreground-window process detection.
- **Actions:** system volume, mute, microphone mute, media transport, scroll, keyboard
  shortcuts, launch app/file/URL, and run shell command.
- **LED control:** off, solid, software pulse, flash, and volume-tracking with fade —
  driven via interrupt-OUT (WinUSB-safe).
- **CustomTkinter GUI** (profiles, bindings, LED, timing) and a **system tray** icon.
- **Native Windows audio** via pycaw; **libusb** backend bundled via the `libusb` package.
- **One-file PyInstaller build** (`build.spec`); verified end-to-end on real hardware.

### Reverse-engineering findings (Windows / WinUSB)
- The device must be on the **WinUSB** driver (Zadig); the default HID driver blocks
  low-level transfers.
- Do **not** call `set_configuration()` on Windows — it invalidates the endpoint pipes
  ("Entity not found").
- Set the LED via **interrupt-OUT** (endpoint `0x02`), not a vendor control transfer —
  the control transfer permanently breaks the interrupt-IN read pipe on WinUSB until a
  power cycle. Hardware pulse modes are therefore disabled on Windows; pulse/flash are
  done in software.

[Unreleased]: https://github.com/curtana/griffinpowermate/compare/v1.3.1...develop
[1.3.1]: https://github.com/curtana/griffinpowermate/releases/tag/v1.3.1
[1.3.0]: https://github.com/curtana/griffinpowermate/releases/tag/v1.3.0
[1.2.0]: https://github.com/accolytebyte/griffinpowermate/releases/tag/v1.2.0
[1.1.1]: https://github.com/accolytebyte/griffinpowermate/releases/tag/v1.1.1
[1.1.0]: https://github.com/accolytebyte/griffinpowermate/releases/tag/v1.1.0
[1.0.0]: https://github.com/accolytebyte/griffinpowermate/releases/tag/v1.0.0
