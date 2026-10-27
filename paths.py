"""App, data, and bundled-resource locations (independent of the working directory).

- app folder: next to the .exe when frozen by PyInstaller, else the folder holding
  main.py. The CWD is never used (autostart runs with CWD = System32).
- data folder (config.json, powermate.log): the app folder if it is writable,
  otherwise %LOCALAPPDATA%\\PowerMate. Decided once, on first use.
"""
import os
import sys
import tempfile
from typing import Optional

APP_NAME = "PowerMate"

_data_dir: Optional[str] = None
_fallback_used = False


def app_dir() -> str:
    """Folder the app runs from: the .exe's folder when frozen, else main.py's."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _is_writable(folder: str) -> bool:
    """True if a file can actually be created (and removed) in `folder`."""
    try:
        fd, probe = tempfile.mkstemp(prefix=".powermate-write-test-", dir=folder)
    except OSError:
        return False
    os.close(fd)
    try:
        os.remove(probe)
    except OSError:
        pass
    return True


def _fallback_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, APP_NAME)


def data_dir() -> str:
    """Folder for config.json / powermate.log (cached after the first call)."""
    global _data_dir, _fallback_used
    if _data_dir is None:
        folder = app_dir()
        if _is_writable(folder):
            _fallback_used = False
        else:
            fallback = _fallback_dir()
            try:
                os.makedirs(fallback, exist_ok=True)
                folder, _fallback_used = fallback, True
            except OSError:
                _fallback_used = False   # nowhere better; keep the app folder
        _data_dir = folder
    return _data_dir


def fallback_used() -> bool:
    """True if data_dir() had to use %LOCALAPPDATA%\\PowerMate."""
    data_dir()
    return _fallback_used


def data_path(name: str) -> str:
    """Path of a user data file (config.json, powermate.log)."""
    return os.path.join(data_dir(), name)


def resource_path(rel: str) -> str:
    """Resolve a bundled resource path, working both from source and inside a
    PyInstaller one-file bundle (which extracts data to sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None) or app_dir()
    return os.path.join(base, rel)
