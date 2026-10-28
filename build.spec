# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a one-file PowerMate.exe.

Build with:  pyinstaller build.spec
Output:      dist/PowerMate.exe
"""
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --- Locate the bundled libusb-1.0.dll from the `libusb` pip package ---
def _libusb_dll():
    import libusb
    arch = "x86_64" if sys.maxsize > 2 ** 32 else "x86"
    base = os.path.dirname(libusb.__file__)
    cand = os.path.join(base, "_platform", "windows", arch, "libusb-1.0.dll")
    if not os.path.exists(cand):
        raise FileNotFoundError(f"libusb-1.0.dll not found at {cand}")
    return cand

binaries = [(_libusb_dll(), ".")]

# CustomTkinter ships theme/asset JSON + images that must be bundled.
datas = [("assets/icon.ico", "assets")]
datas += collect_data_files("customtkinter")

# --- EXE file properties (VERSIONINFO) ---
# Generated from version.py on every build; no hand-kept resource file.
import importlib.util
_SPEC_ROOT = os.path.dirname(os.path.abspath(SPEC))
_vspec = importlib.util.spec_from_file_location(
    "_pm_version", os.path.join(_SPEC_ROOT, "version.py"))
_vmod = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(_vmod)
_BUILD_INFO_DIR = os.path.join(_SPEC_ROOT, "build", "_build_info")
os.makedirs(_BUILD_INFO_DIR, exist_ok=True)
_VERSION_FILE = os.path.join(_BUILD_INFO_DIR, "version_info.txt")
with open(_VERSION_FILE, "w", encoding="utf-8") as _fp:
    _fp.write(_vmod.version_info_text())

hiddenimports = [
    "comtypes",
    "pycaw",
    "pystray._win32",
    "PIL._tkinter_finder",
    "win32gui",
    "win32process",
    "win32api",
]
hiddenimports += collect_submodules("comtypes")
hiddenimports += collect_submodules("pycaw")

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PowerMate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets\\icon.ico",
    version=_VERSION_FILE,
)
