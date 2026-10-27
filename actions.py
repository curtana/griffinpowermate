"""Action executors: what happens when a trigger fires."""
import logging
import subprocess
import os
import time
import ctypes
from typing import Callable, Any, Dict
from audio import AudioController
import keyboard

logger = logging.getLogger(__name__)

# Mouse wheel injection (user32.mouse_event). One wheel notch is WHEEL_DELTA (120);
# Windows' default "lines per notch" is 3, so one scroll line is 40 delta units.
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
LINES_PER_NOTCH = 3
DELTA_PER_LINE = WHEEL_DELTA / LINES_PER_NOTCH


def _mouse_event(flags: int, dx: int, dy: int, data: int, extra: int):
    """Thin wrapper over user32.mouse_event (tests replace this)."""
    ctypes.windll.user32.mouse_event(flags, dx, dy, ctypes.c_ulong(data & 0xFFFFFFFF), extra)


class ActionExecutor:
    """Execute actions triggered by PowerMate events."""

    def __init__(self, audio: AudioController, on_led_change: Callable = None):
        self.audio = audio
        self.on_led_change = on_led_change or (lambda: None)
        # Signed fractional wheel delta not yet sent (carried to the next scroll).
        self._scroll_remainder = 0.0

    def execute(self, action_config: Dict[str, Any], amount: int = 0):
        """
        Execute an action based on configuration.
        action_config: {"action": "...", <other params>}
        amount: rotation delta (for scroll/volume actions)
        """
        if not action_config:
            return

        action = action_config.get("action", "none")

        try:
            if action == "none":
                pass

            elif action == "volume_up":
                self._volume_up((amount or 1) * action_config.get("amount", 0.04))

            elif action == "volume_down":
                self._volume_down((amount or 1) * action_config.get("amount", 0.04))

            elif action == "mute":
                self._mute_toggle()

            elif action == "mic_mute_toggle":
                self._mic_mute_toggle()

            elif action == "media_play_pause":
                self._media_key("play/pause media")

            elif action == "media_next":
                self._media_key("next track")

            elif action == "media_prev":
                self._media_key("previous track")

            elif action == "scroll_up":
                self._scroll((amount or 1) * action_config.get("amount", 3), direction="up")

            elif action == "scroll_down":
                self._scroll((amount or 1) * action_config.get("amount", 3), direction="down")

            elif action == "key":
                keys = action_config.get("keys", "")
                self._send_keys(keys)

            elif action == "macro":
                self._macro(action_config.get("sequence", []))

            elif action == "launch":
                path = action_config.get("path", "")
                self._launch(path)

            elif action == "run":
                cmd = action_config.get("command", "")
                self._run_command(cmd)

        except Exception as e:
            logger.error(f"Error executing action {action}: {e}")

    def _volume_up(self, amount: float):
        """Increase volume."""
        vol = self.audio.get_volume()
        self.audio.set_volume(vol + amount)
        self.on_led_change()

    def _volume_down(self, amount: float):
        """Decrease volume."""
        vol = self.audio.get_volume()
        self.audio.set_volume(vol - amount)
        self.on_led_change()

    def _mute_toggle(self):
        """Toggle mute."""
        self.audio.toggle_mute()
        self.on_led_change()

    def _mic_mute_toggle(self):
        """Toggle microphone mute."""
        self.audio.toggle_mic_mute()

    def _media_key(self, key: str):
        """Send media key (play pause, next, previous)."""
        try:
            keyboard.send(key)
        except Exception as e:
            logger.debug(f"Error sending media key {key}: {e}")

    def _scroll(self, amount: float, direction: str):
        """Scroll up or down by `amount` lines (3 lines = 1 wheel notch). No sleep."""
        lines = amount if direction == "up" else -amount  # wheel: + up, - down
        # Signed carry: rounding keeps |remainder| <= 0.5 delta units, so a direction
        # change carries at most half a unit (1/240 notch) into the new direction.
        total = lines * DELTA_PER_LINE + self._scroll_remainder
        delta = int(round(total))
        self._scroll_remainder = total - delta
        if delta == 0:
            return
        try:
            _mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        except Exception as e:
            logger.debug(f"Error scrolling: {e}")

    def _send_keys(self, keys: str):
        """Send keyboard shortcut."""
        if not keys:
            return
        try:
            keyboard.send(keys)
        except Exception as e:
            logger.debug(f"Error sending keys '{keys}': {e}")

    def _macro(self, sequence):
        """Send a sequence of key combos in order (a keyboard macro)."""
        if not sequence:
            return
        for combo in sequence:
            combo = (combo or "").strip()
            if not combo:
                continue
            self._send_keys(combo)
            time.sleep(0.05)

    def _launch(self, path: str):
        """Launch file/application/URL."""
        if not path:
            return
        try:
            os.startfile(path)
        except Exception as e:
            logger.debug(f"Error launching {path}: {e}")

    def _run_command(self, command: str):
        """Run shell command."""
        if not command:
            return
        try:
            subprocess.Popen(command, shell=True)
        except Exception as e:
            logger.debug(f"Error running command '{command}': {e}")
