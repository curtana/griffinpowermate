"""Tests for the EXE file properties (issue #14): version.py builds the VERSIONINFO
resource that build.spec links into PowerMate.exe."""
import os, re, sys, traceback

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import version as ver

fails = []
def check(name, fn):
    print(f"... {name}", flush=True)
    try:
        fn(); print(f"[PASS] {name}", flush=True)
    except Exception as e:
        fails.append(name); print(f"[FAIL] {name}: {e}"); traceback.print_exc()


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def test_1_file_version_tuple():
    assert ver.file_version_tuple("1.3.1") == (1, 3, 1, 0)
    assert ver.file_version_tuple("2") == (2, 0, 0, 0)
    assert ver.file_version_tuple("1.2.3.4") == (1, 2, 3, 4)
    for bad in ("", "1.x.3", "1.2.3.4.5", "1.-2.3", "1.70000.0"):
        try:
            ver.file_version_tuple(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should raise ValueError")


def test_2_text_has_product_name_and_version():
    t = ver.version_info_text()
    tup = ver.file_version_tuple(ver.VERSION)
    assert f"filevers={tup!r}" in t and f"prodvers={tup!r}" in t, t
    assert f"StringStruct('ProductName', {ver.PRODUCT_NAME!r})" in t, t
    assert f"StringStruct('FileVersion', '{ver.VERSION}')" in t, t
    assert f"StringStruct('ProductVersion', '{ver.VERSION} ({ver.RELEASE_DATE})')" in t, t
    assert "StringStruct('OriginalFilename', 'PowerMate.exe')" in t, t
    for key in ("CompanyName", "LegalCopyright", "LegalTrademarks"):
        assert key not in t, f"unverified field {key} present"


def test_3_parses_with_pyinstaller():
    try:
        from PyInstaller.utils.win32 import versioninfo as vi
    except Exception as e:  # PyInstaller not installed
        print(f"    (skipped: PyInstaller not available: {e})")
        return
    ns = {k: getattr(vi, k) for k in dir(vi)}
    info = eval(ver.version_info_text("1.2.3", "2026-01-02"), ns)
    assert info.ffi.fileVersionMS == (1 << 16) | 2, info.ffi.fileVersionMS
    assert info.ffi.fileVersionLS == (3 << 16) | 0, info.ffi.fileVersionLS
    assert "1.2.3 (2026-01-02)" in str(info)


def test_4_spec_links_version_resource():
    spec = _read("build.spec")
    assert "version_info_text" in spec, "build.spec does not generate the resource"
    assert "version=_VERSION_FILE" in spec, "EXE(version=...) not linked"


def test_5_version_matches_newest_changelog_release():
    # The newest dated section ("## [X.Y.Z] — YYYY-MM-DD") is the release being built.
    m = re.search(r"^## \[(\d+(?:\.\d+)*)\] — (\d{4}-\d{2}-\d{2})", _read("CHANGELOG.md"), re.M)
    assert m, "no dated release section in CHANGELOG.md"
    assert (m.group(1), m.group(2)) == (ver.VERSION, ver.RELEASE_DATE), (
        f"CHANGELOG {m.group(1)} — {m.group(2)} != version.py {ver.VERSION} — {ver.RELEASE_DATE}")


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        check(name, fn)

print("\n" + ("ALL VERSION TESTS PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
