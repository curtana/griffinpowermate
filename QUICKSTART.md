# PowerMate Quick Start Guide

## First-Time Setup (5 minutes)

### 1. Install Python Dependencies
Skip this if you use the prebuilt `PowerMate.exe` from the
[Releases](https://github.com/curtana/griffinpowermate/releases) page.

Use **Python 3.11 (64-bit)** — the pinned requirements have no wheels for 3.14:
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Or use the installer batch file:
```bash
install.bat
```

### 2. Install USB Driver (Zadig)

This is a one-time setup required for the PowerMate to work.

1. Download **Zadig** from https://zadig.akeo.ie/
2. **Plug in your PowerMate** USB knob
3. Run Zadig (no admin required)
4. Click **Options** → **List All Devices**
5. In the dropdown, select **Griffin PowerMate**
6. Choose **WinUSB** as the replacement driver
7. Click **Replace Driver** and wait for completion
8. You may see an error — that's normal; the driver was still installed

**You only need to do this once.**

### 3. Run the Application

From the command line:
```bash
python main.py
```

Or build and run as a standalone EXE:
```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean build.spec
```
Then run `dist\PowerMate.exe` (its file properties show the product name and version).

## What You'll See

1. **System Tray Icon** appears (blue, in the bottom-right corner)
2. **Settings Window** may open (depends on first run)
3. **Status Bar** shows "PowerMate connected" (green) if device found, or "PowerMate not found" (red) if not

## Basic Usage

### Rotate the Knob
- **Rotate clockwise** → increases system volume
- **Rotate counter-clockwise** → decreases system volume
- **LED** lights at the volume level while the volume changes, then fades after 1 second
  (it doesn't light for scroll or key actions, and looks dark when muted/near 0 %)

### Press the Knob
- **Single click** → mute/unmute
- **Double click** → play/pause
- **Triple click** → next track
- **Long press** (hold 0.5 seconds) → toggle microphone mute

### Rotate While Pressing
- **Hold + rotate clockwise** → scroll down (web pages, etc.)
- **Hold + rotate counter-clockwise** → scroll up

## Customize Behavior

### Using the GUI
1. Right-click the tray icon and click **Settings**
2. **Profiles** tab on the left:
   - Default profile applies to all apps
   - **+ Add Profile** to create app-specific bindings
3. **Bindings** tab: edit trigger → action for each profile
4. **LED** tab: change LED mode (solid, pulse, flash, volume-track)
5. **Timing** tab: adjust gesture timing, rotation sensitivity, and rotation
   acceleration (0 = off; higher = fast turns move further)
6. Click **Save** to persist

### Editing config.json by Hand
- Close the app
- Open `config.json` in a text editor
- Modify the profiles, actions, LED settings, timing
- Save the file
- Restart the app (or it auto-reloads in a few seconds)

## Common Customizations

### Make PowerMate control video playback in Chrome
1. Open Settings → click "+ Add Profile" → select `chrome.exe`
2. In the **Bindings** tab, for the Chrome profile:
   - `rotate_right`: `scroll_down`, amount=5 (lines; 3 lines = one wheel notch)
   - `rotate_left`: `scroll_up`, amount=5
   - `long_press`: `key`, keys="f5" (refresh)
   - `double_press`: `key`, keys="space" (play/pause)
3. Click **Save**

### Custom Keyboard Shortcut
1. In the Bindings tab, select a trigger
2. Choose action `key`
3. In the **Keys** field, enter the shortcut (e.g., `"ctrl+z"` for undo)
4. Click **Save**

### Launch an App
1. In the Bindings tab, select a trigger
2. Choose action `launch`
3. In the **Path** field, enter:
   - `"C:\Program Files\VLC\vlc.exe"` (full path to app)
   - `"https://www.youtube.com"` (URL)
   - `"C:\Users\YourName\Documents\file.pdf"` (file to open)
4. Click **Save**

## Troubleshooting

### "PowerMate not found" — red status
1. Check the USB cable is plugged in
2. Verify Zadig driver is installed (see **Install USB Driver** above)
3. Try unplugging and replugging the knob
4. Restart the app

### Actions don't fire
1. Ensure the correct profile is active (status bar shows app name)
2. Click the profile in the Settings window to verify bindings
3. For keyboard shortcuts, ensure the target app has focus
4. Restart the app

### LED doesn't respond
1. Check LED mode is not `off` (LED tab)
2. If in `solid` mode, ensure brightness slider is not at 0
3. In `volume` mode the LED only lights on volume/mute actions — if the focused app's
   profile maps rotation to scroll, or the system is muted/near 0 %, it stays dark
4. Try switching to `solid` or `flash` mode to verify the device responds
   (pulse and flash are done in software on Windows)

## Tips

- **Pause/Resume**: Right-click the tray icon and click **Pause** to temporarily disable the knob. Click **Resume** to re-enable.
- **Auto-reload**: Edit `config.json` by hand and save — the app auto-reloads within 1 second (you'll see "Status: Config reloaded"). Invalid values fall back to defaults with a warning in `powermate.log`.
- **Per-app profiles**: Profiles inherit from `default`, so you only need to override the triggers you customize.
- **Multiple PowerMates**: The current version supports one PowerMate. Multiple support would require device selection in the GUI (future enhancement).

## Need Help?

1. Check `powermate.log` next to the app (or `%LOCALAPPDATA%\PowerMate` if the app
   folder isn't writable) for error messages
2. Open Settings and check the status bar for connection state
3. Run with debug logging:
   ```bash
   python main.py debug
   ```
   This prints detailed logs to the console

---

**Enjoy your PowerMate!** 🎛️
