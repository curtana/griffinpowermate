"""Program version: the single source for the EXE's file properties.

Bump VERSION and RELEASE_DATE before tagging a release (tag ``v<VERSION>``);
``_test_version.py`` checks they match the newest dated CHANGELOG section.
``build.spec`` turns them into the Windows VERSIONINFO resource on every build.
"""

VERSION = "1.3.1"
RELEASE_DATE = "2026-10-28"

# Shown in Explorer > Properties > Details (Product name / File description).
PRODUCT_NAME = "Griffin PowerMate Controller"
ORIGINAL_FILENAME = "PowerMate.exe"


def file_version_tuple(version: str) -> tuple:
    """``"1.3.1"`` -> ``(1, 3, 1, 0)``: the 4-part VERSIONINFO file version.

    Missing parts are padded with 0. Non-numeric parts, more than 4 parts or a
    part above 65535 raise ValueError so a bad constant can't slip into a build."""
    parts = str(version).strip().split(".")
    if not 1 <= len(parts) <= 4 or not all(p.isdigit() for p in parts):
        raise ValueError(f"VERSION is not like 1.3.1: {version!r}")
    nums = [int(p) for p in parts]
    if any(n > 65535 for n in nums):
        raise ValueError(f"VERSION part above 65535: {version!r}")
    return tuple(nums + [0] * (4 - len(nums)))


def version_info_text(version: str = VERSION, release_date: str = RELEASE_DATE) -> str:
    """Contents of the PyInstaller ``version=`` file (a ``VSVersionInfo(...)`` expression).

    File/product version are ``X.Y.Z.0``; the product version string also carries
    the release date. Unverified fields (company, copyright) are left out."""
    tup = file_version_tuple(version)
    text_ver = ".".join(str(n) for n in (tup[:3] if tup[3] == 0 else tup))
    prod_ver = f"{text_ver} ({release_date})"
    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={tup!r}, prodvers={tup!r}, mask=0x3f, flags=0x0,\n"
        "    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904b0', [\n"
        f"      StringStruct('FileDescription', {PRODUCT_NAME!r}),\n"
        f"      StringStruct('FileVersion', {text_ver!r}),\n"
        f"      StringStruct('OriginalFilename', {ORIGINAL_FILENAME!r}),\n"
        f"      StringStruct('ProductName', {PRODUCT_NAME!r}),\n"
        f"      StringStruct('ProductVersion', {prod_ver!r})])]),\n"
        "    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])\n"
        "  ]\n"
        ")\n"
    )
