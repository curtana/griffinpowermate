"""Configuration management: load, save, validate config.json."""
import copy
import json
import logging
import math
import threading
import os
from typing import Dict, Any, Optional

from paths import data_path

logger = logging.getLogger(__name__)

# File name only; the folder comes from paths.data_path() when the Config is
# created (next to the app, not the CWD — see paths.py).
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "timing": {
        "multi_click_ms": 250,
        "long_press_ms": 500,
        "rotate_sensitivity": 1.0,
        "rotate_acceleration": 0.0
    },
    "led": {
        "mode": "volume",
        "brightness": 1.0,
        "pulse_speed": 255,
        "pulse_table": 0,
        "flash_on_ms": 200,
        "flash_off_ms": 800,
        "fade_speed": 1.0,
        "fade_delay": 1.0
    },
    "start_with_windows": False,
    "osd": {
        "enabled": True,
        "opacity": 0.85,
        "duration_ms": 1200,
        "bg_color": "#1a1a1a",
        "font_family": "Segoe UI",
        "font_color": "#ffffff",
        "font_size": 15
    },
    "profiles": {
        "default": {
            "rotate_right": {"action": "volume_up", "amount": 0.04},
            "rotate_left": {"action": "volume_down", "amount": 0.04},
            "press": {"action": "mute"},
            "double_press": {"action": "media_play_pause"},
            "triple_press": {"action": "media_next"},
            "long_press": {"action": "mic_mute_toggle"},
            "press_rotate_right": {"action": "scroll_down", "amount": 3},
            "press_rotate_left": {"action": "scroll_up", "amount": 3}
        },
        "spotify.exe": {
            "press": {"action": "key", "keys": "space"},
            "double_press": {"action": "key", "keys": "ctrl+right"},
            "triple_press": {"action": "key", "keys": "ctrl+left"},
            "long_press": {"action": "mute"},
            "press_rotate_right": {"action": "key", "keys": "ctrl+right"},
            "press_rotate_left": {"action": "key", "keys": "ctrl+left"}
        }
    }
}


# --- Validation ------------------------------------------------------------
# A bad value is replaced by its default *in memory* and logged; validation
# never rewrites the user's config.json. Numeric strings ("250") are accepted
# and converted; bools are not numbers here.

# Default `amount` per action (the same values actions.py falls back to).
DEFAULT_ACTION_AMOUNTS = {
    "volume_up": 0.04,
    "volume_down": 0.04,
    "scroll_up": 3,
    "scroll_down": 3,
}

LED_MODES = ("off", "solid", "pulse", "flash", "volume")


def to_number(value: Any):
    """int/float or a numeric string as a finite int/float; None otherwise.

    Strings parse as int when they can, else float. Bools are rejected."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            num = int(text)
        except ValueError:
            try:
                num = float(text)
            except ValueError:
                return None
    else:
        return None
    if isinstance(num, float) and not math.isfinite(num):
        return None
    return num


def _int_rule(lo: int, hi: Optional[int] = None):
    def check(value):
        num = to_number(value)
        if num is None:
            return None
        num = int(round(num))
        if num < lo or (hi is not None and num > hi):
            return None
        return num
    return check


def _float_rule(lo: float, hi: Optional[float] = None, lo_open: bool = False):
    def check(value):
        num = to_number(value)
        if num is None:
            return None
        num = float(num)
        if num < lo or (lo_open and num == lo) or (hi is not None and num > hi):
            return None
        return num
    return check


def _mode_rule(value):
    return value if value in LED_MODES else None


# Validated settings: path -> check(value) giving the cleaned value, or None
# when invalid. Paths not listed here (osd.*, start_with_windows) pass as-is.
_RULES = {
    "timing.multi_click_ms": _int_rule(50, 2000),
    "timing.long_press_ms": _int_rule(100, 5000),
    "timing.rotate_sensitivity": _float_rule(0.0, 10.0, lo_open=True),
    "timing.rotate_acceleration": _float_rule(0.0, 1.0),
    "led.mode": _mode_rule,
    "led.brightness": _float_rule(0.0, 1.0),
    "led.pulse_speed": _int_rule(0, 510),
    "led.pulse_table": _int_rule(0, 2),
    "led.flash_on_ms": _int_rule(0),
    "led.flash_off_ms": _int_rule(0),
    "led.fade_speed": _float_rule(0.0, lo_open=True),
    "led.fade_delay": _float_rule(0.0),
}


def _default_for(path: str) -> Any:
    value = DEFAULT_CONFIG
    for part in path.split("."):
        value = value[part]
    return copy.deepcopy(value)


def _warn(path: str, value: Any, default: Any):
    logger.warning(f"config: {path} = {value!r} invalid, using {default!r}")


def _clean_value(path: str, value: Any):
    """(cleaned value, ok) for a path in _RULES; ok=False means the default
    was substituted."""
    cleaned = _RULES[path](value)
    if cleaned is None:
        default = _default_for(path)
        _warn(path, value, default)
        return default, False
    return cleaned, True


def _clean_flash_pair(led: Dict[str, Any]) -> bool:
    """flash_on_ms + flash_off_ms must be > 0, else both get their defaults.
    Returns False when that happened."""
    on, off = led.get("flash_on_ms"), led.get("flash_off_ms")
    if not isinstance(on, int) or not isinstance(off, int) or on + off > 0:
        return True  # a missing key falls back to its (non-zero) default
    d_on, d_off = _default_for("led.flash_on_ms"), _default_for("led.flash_off_ms")
    logger.warning(f"config: led.flash_on_ms + led.flash_off_ms = {on} + {off} invalid, "
                   f"using {d_on} + {d_off}")
    led["flash_on_ms"], led["flash_off_ms"] = d_on, d_off
    return False


def _clean_action(where: str, action: Any):
    """Clean one trigger binding in place. Empty / 'none' / other bindings
    (and the inheritance they mean) are left exactly as they are."""
    if not isinstance(action, dict):
        return
    name = action.get("action")
    if name in DEFAULT_ACTION_AMOUNTS and "amount" in action:
        num = to_number(action["amount"])
        if num is None:
            default = DEFAULT_ACTION_AMOUNTS[name]
            _warn(f"{where}.amount", action["amount"], default)
            num = default
        action["amount"] = num
    elif name == "key" and "keys" in action:
        keys = action["keys"]
        if not isinstance(keys, str):
            if to_number(keys) is not None:
                action["keys"] = str(keys)    # 1 -> "1", not scan code 1 (Esc)
            else:
                _warn(f"{where}.keys", keys, "")
                action["keys"] = ""
    elif name == "macro" and "sequence" in action:
        seq = action["sequence"]
        if isinstance(seq, list):
            cleaned = [step if isinstance(step, str) else str(step)
                       for step in seq
                       if isinstance(step, str) or to_number(step) is not None]
        elif isinstance(seq, str):
            cleaned = [seq]   # one combo, not one key per character
        else:
            cleaned = []
        if not isinstance(seq, list) or len(cleaned) != len(seq):
            _warn(f"{where}.sequence", seq, cleaned)
        action["sequence"] = cleaned


def sanitize(config: Any) -> Dict[str, Any]:
    """Return a validated copy of a loaded config (rules above).

    Missing keys stay missing (readers supply defaults); unknown keys are kept."""
    if not isinstance(config, dict):
        logger.warning(f"config: top level is {type(config).__name__}, not an object; "
                       f"using defaults")
        return copy.deepcopy(DEFAULT_CONFIG)
    config = copy.deepcopy(config)
    for section in ("timing", "led"):
        if section not in config:
            continue
        values = config[section]
        if not isinstance(values, dict):
            default = _default_for(section)
            _warn(section, values, default)
            config[section] = default
            continue
        for key in list(values):
            path = f"{section}.{key}"
            if path in _RULES:
                values[key], _ = _clean_value(path, values[key])
    if isinstance(config.get("led"), dict):
        _clean_flash_pair(config["led"])
    profiles = config.get("profiles")
    if isinstance(profiles, dict):
        for pname, bindings in profiles.items():
            if isinstance(bindings, dict):
                for trigger, action in bindings.items():
                    _clean_action(f"profiles.{pname}.{trigger}", action)
    return config


class Config:
    """Configuration loader and manager."""

    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or data_path(CONFIG_FILE)
        self.config: Dict[str, Any] = {}
        # Reentrant: reload() holds the lock and may call save() (first run).
        self._lock = threading.RLock()
        self._callbacks = []
        self._last_mtime = 0

        # Load config (creates a default file on first run)
        self.reload()

        # Watch for file changes
        self._watch_thread = threading.Thread(target=self._watch_file, daemon=True)
        self._watch_thread.start()

    def reload(self):
        """Reload config from file, or create default if missing."""
        with self._lock:
            if os.path.exists(self.config_file):
                try:
                    with open(self.config_file, 'r') as f:
                        # Validated copy in memory; the file stays as written.
                        self.config = sanitize(json.load(f))
                    logger.info(f"Loaded config from {self.config_file}")
                    self._last_mtime = os.path.getmtime(self.config_file)
                except Exception as e:
                    logger.error(f"Error loading config: {e}")
                    # deepcopy so edits don't mutate the module-level defaults.
                    self.config = copy.deepcopy(DEFAULT_CONFIG)
                    # Mark this mtime as handled so the watcher doesn't reload the
                    # broken file every second (avoids log/CPU spam).
                    try:
                        self._last_mtime = os.path.getmtime(self.config_file)
                    except OSError:
                        pass
            else:
                self.config = copy.deepcopy(DEFAULT_CONFIG)
                self.save()

    def save(self):
        """Save current config to file."""
        with self._lock:
            try:
                with open(self.config_file, 'w') as f:
                    json.dump(self.config, f, indent=2)
                logger.info(f"Saved config to {self.config_file}")
                self._last_mtime = os.path.getmtime(self.config_file)
            except Exception as e:
                logger.error(f"Error saving config: {e}")

    def get(self, path: str, default: Any = None) -> Any:
        """Get value by dot-separated path (e.g. "timing.multi_click_ms")."""
        with self._lock:
            parts = path.split(".")
            value = self.config
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                    if value is None:
                        return default
                else:
                    return default
            return value

    def set(self, path: str, value: Any) -> bool:
        """Set value by dot-separated path.

        Validated settings (timing.*, led.*) go through the same rules as the
        file: an invalid value is replaced by its default and logged. Returns
        False when that happened, True otherwise. The flash on/off pair rule
        spans two keys, so callers setting them run check_flash_pair() after."""
        with self._lock:
            ok = True
            if path in _RULES:
                value, ok = _clean_value(path, value)
            parts = path.split(".")
            target = self.config
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value
            return ok

    def check_flash_pair(self) -> bool:
        """Apply the led.flash_on_ms + led.flash_off_ms > 0 rule (both get
        defaults otherwise). Returns False when the defaults were applied."""
        with self._lock:
            led = self.config.get("led")
            return _clean_flash_pair(led) if isinstance(led, dict) else True

    def get_profile(self, process_name: Optional[str]) -> Dict[str, Any]:
        """Get effective bindings for a process.

        App-specific profiles inherit from 'default' for any trigger they
        don't explicitly override (a trigger set to {"action": "none"} counts
        as an explicit override).
        """
        with self._lock:
            profiles = self.config.get("profiles", {})
            default = profiles.get("default", {})
            if process_name and process_name in profiles and process_name != "default":
                merged = dict(default)
                merged.update(profiles[process_name])
                return merged
            return dict(default)

    def get_profile_raw(self, process_name: str) -> Dict[str, Any]:
        """Get a profile's own bindings without default inheritance (for editing)."""
        with self._lock:
            return dict(self.config.get("profiles", {}).get(process_name, {}))

    def add_profile(self, name: str):
        """Add a new profile (inherits from default)."""
        with self._lock:
            if "profiles" not in self.config:
                self.config["profiles"] = {}
            if name not in self.config["profiles"]:
                self.config["profiles"][name] = {}

    def delete_profile(self, name: str):
        """Delete a profile (except 'default')."""
        if name == "default":
            logger.warning("Cannot delete 'default' profile")
            return
        with self._lock:
            if "profiles" in self.config and name in self.config["profiles"]:
                del self.config["profiles"][name]

    def get_profiles(self) -> list:
        """Get list of profile names."""
        with self._lock:
            return list(self.config.get("profiles", {}).keys())

    def on_change(self, callback):
        """Register callback to be called when config changes."""
        self._callbacks.append(callback)

    def _watch_file(self):
        """Watch config file for external changes."""
        while True:
            try:
                if os.path.exists(self.config_file):
                    mtime = os.path.getmtime(self.config_file)
                    if mtime != self._last_mtime:
                        self.reload()
                        for callback in self._callbacks:
                            try:
                                callback()
                            except Exception as e:
                                logger.debug(f"Error in config change callback: {e}")
            except Exception as e:
                logger.debug(f"Error watching config file: {e}")

            import time
            time.sleep(1)
