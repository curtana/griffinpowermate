"""Tests for shortcut recording (issue #18): the recorded combo must hold only the
modifiers really pressed (NumLock / CapsLock ignored) and key names that
keyboard.send() understands. No real key input is ever sent."""
import os, sys, time, threading, traceback

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

fails = []
def check(name, fn):
    print(f"... {name}", flush=True)
    try:
        fn(); print(f"[PASS] {name}", flush=True)
    except Exception as e:
        fails.append(name); print(f"[FAIL] {name}: {e}"); traceback.print_exc()

def watchdog():
    time.sleep(60); print("[WATCHDOG] timeout"); os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()

SHIFT, CAPS, CTRL, MOD1, ALT_WIN = 0x0001, 0x0002, 0x0004, 0x0008, 0x20000


def _combo(keysym, state, keycode=None, platform="win32"):
    from ui.action_editor import ActionEditor
    return ActionEditor.combo_from_key(keysym, state, keycode, platform)


def _eq(got, want):
    assert got == want, f"got {got!r}, want {want!r}"


# (1) NumLock (0x0008 on Windows Tk) must not become Alt.
def test_win_numlock_is_not_alt():
    _eq(_combo("z", MOD1 | CTRL), "ctrl+z")
    _eq(_combo("z", MOD1 | CTRL, 0x5A), "ctrl+z")

# (2) Real Alt on Windows is 0x20000.
def test_win_alt():
    _eq(_combo("z", ALT_WIN | CTRL), "ctrl+alt+z")
    _eq(_combo("z", ALT_WIN | CTRL | MOD1, 0x5A), "ctrl+alt+z")

def test_non_windows_mod1_still_alt():
    _eq(_combo("z", MOD1 | CTRL, platform="linux"), "ctrl+alt+z")

# (3) Shift+1 -> shift+1, by keysym and by virtual-key code.
def test_shift_digit():
    _eq(_combo("exclam", SHIFT), "shift+1")
    _eq(_combo("exclam", SHIFT, 0x31), "shift+1")
    _eq(_combo("parenright", SHIFT | CTRL, 0x30), "ctrl+shift+0")

# (4) Shift+- -> shift+-
def test_shift_minus():
    _eq(_combo("underscore", SHIFT), "shift+-")
    _eq(_combo("underscore", SHIFT, 0xBD), "shift+-")

def test_shifted_symbols_map_to_base_key():
    us = {"exclam": "1", "at": "2", "numbersign": "3", "dollar": "4",
          "percent": "5", "asciicircum": "6", "ampersand": "7", "asterisk": "8",
          "parenleft": "9", "parenright": "0", "underscore": "-", "plus": "=",
          "braceleft": "[", "braceright": "]", "bar": "\\", "colon": ";",
          "quotedbl": "'", "less": ",", "greater": ".", "question": "/",
          "asciitilde": "`"}
    for ks, base in us.items():
        _eq(_combo(ks, SHIFT), "shift+" + base)

def test_unshifted_punctuation():
    plain = {"minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]",
             "backslash": "\\", "semicolon": ";", "apostrophe": "'", "comma": ",",
             "period": ".", "slash": "/", "grave": "`"}
    for ks, name in plain.items():
        _eq(_combo(ks, CTRL), "ctrl+" + name)

# (5) CapsLock on + 'A' -> 'a', no shift.
def test_capslock_ignored():
    _eq(_combo("A", CAPS), "a")
    _eq(_combo("A", CAPS, 0x41), "a")
    _eq(_combo("A", CAPS | SHIFT | CTRL, 0x41), "ctrl+shift+a")

# Modifier order stays ctrl+shift+alt+<key>.
def test_modifier_order():
    _eq(_combo("Z", ALT_WIN | SHIFT | CTRL, 0x5A), "ctrl+shift+alt+z")

# (7) Existing keysym map kept.
def test_existing_map_kept():
    for ks, name in {"Return": "enter", "Prior": "page up", "Next": "page down",
                     "BackSpace": "backspace", "Delete": "delete", "Up": "up",
                     "Tab": "tab", "Home": "home", "End": "end",
                     "space": "space"}.items():
        _eq(_combo(ks, CTRL), "ctrl+" + name)
    _eq(_combo("F5", 0), "f5")

# (6) Every produced name is known to 'keyboard' (name lookup only; nothing sent).
def test_names_known_to_keyboard():
    import keyboard
    from ui.action_editor import ActionEditor
    names = set(ActionEditor._KEYSYM_MAP.values())
    names |= {chr(c).lower() for c in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B))}
    names |= {"ctrl", "shift", "alt"}
    bad = []
    for n in sorted(names):
        try:
            if not keyboard.key_to_scan_codes(n):
                bad.append(n)
        except Exception as e:
            bad.append(f"{n} ({e})")
    assert not bad, f"unknown to keyboard: {bad}"
    for combo in ("ctrl+z", "shift+1", "shift+-", "ctrl+shift+alt+z", "ctrl+page up"):
        keyboard.parse_hotkey(combo)

# The record dialog uses the conversion (Tk-internal synthetic events only).
def test_record_dialog_uses_conversion():
    import customtkinter as ctk
    from ui.action_editor import ActionEditor
    root = ctk.CTk(); root.withdraw()
    try:
        ed = ActionEditor(root, "test")
        cases, wrong = [], []
        if sys.platform == "win32":
            cases += [("z", MOD1 | CTRL, 0x5A, "ctrl+z"),
                      ("exclam", SHIFT, 0x31, "shift+1")]
        else:
            cases += [("exclam", SHIFT, 10, "shift+1")]
        for keysym, state, keycode, want in cases:
            entry = ctk.CTkEntry(root)
            before = set(ed.winfo_children())
            ed._record_into(entry)
            root.update()
            dialog = [w for w in ed.winfo_children()
                      if w not in before and isinstance(w, ctk.CTkToplevel)]
            assert dialog, "record dialog not opened"
            # Tk routes key events to the focus window; make it the dialog.
            dialog[0].focus_force()
            root.update()
            dialog[0].event_generate("<KeyPress>", keysym=keysym, state=state,
                                     keycode=keycode)
            root.update()
            if entry.get() != want:
                wrong.append(f"{keysym}/{state:#x}: got {entry.get()!r}, want {want!r}")
        assert not wrong, wrong
    finally:
        root.destroy()


for n, f in list(globals().items()):
    if n.startswith("test_") and callable(f):
        check(n, f)

print("\n" + ("ALL RECORD TESTS PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
