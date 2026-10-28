"""Tests for config.json changed outside the app (#21): the change is applied on
the Tk thread and the settings window shows the file's values.

Uses a temp config file, a fake OSD/LED and a real (briefly shown) settings
window; never touches the real config.json, USB device or tray.
Run: python _test_config_reload.py
"""
import sys, os, json, time, shutil, tempfile, threading, traceback, types, logging

fails = []
def check(name, fn):
    print(f"... {name}", flush=True)
    try:
        fn(); print(f"[PASS] {name}", flush=True)
    except Exception as e:
        fails.append(name); print(f"[FAIL] {name}: {e!r}"); traceback.print_exc()

def watchdog():
    time.sleep(60); print("[WATCHDOG] timeout"); os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()

logging.basicConfig(level=logging.CRITICAL)

_real_file_handler = logging.FileHandler
class _NoFile(logging.NullHandler):
    def __init__(self, *a, **k): super().__init__()
logging.FileHandler = _NoFile          # don't create powermate.log on import
try:
    import main
finally:
    logging.FileHandler = _real_file_handler
from config import Config
from gesture import GestureDetector, GestureConfig
from ui.app_window import AppWindow

MAIN_THREAD = threading.get_ident()
TMP_DIR = tempfile.mkdtemp(prefix="cfgreload_")


class FakeOSD:
    """Records each set_options call and the thread it ran on."""
    def __init__(self):
        self.calls = []
    def set_options(self, **kw):
        self.calls.append((threading.get_ident(), kw))
    def show(self, *a):
        pass


def watcher_callback(app):
    # The callback main.py registers with Config.on_change (watcher thread).
    fn = getattr(main.PowerMateApp, "_on_config_file_change", main.PowerMateApp._on_config_change)
    return types.MethodType(fn, app)


def make_app(cfg, with_window=True):
    """A PowerMateApp with only the parts _on_config_change uses (no device/tray)."""
    app = object.__new__(main.PowerMateApp)
    app.config = cfg
    app.osd = FakeOSD()
    app.led = types.SimpleNamespace()
    app.gesture = GestureDetector(lambda *a: None, GestureConfig())
    app.running = True
    app.app_window = None
    if with_window:
        app.app_window = AppWindow(cfg, on_config_change=app._on_config_change)
        app.app_window.create()
    return app


def new_config(name):
    cfg = Config(os.path.join(TMP_DIR, name))
    return cfg


def write_external(cfg, edit):
    """Edit config.json the way another program would; bump the mtime so the
    watcher (1 s poll) sees a change even on a coarse clock."""
    with open(cfg.config_file) as f:
        data = json.load(f)
    edit(data)
    with open(cfg.config_file, "w") as f:
        json.dump(data, f, indent=2)
    t = time.time() + 5
    os.utime(cfg.config_file, (t, t))


def run_until(root, cond, timeout=5.0):
    """Run the real Tk mainloop (other threads' after() needs it) until cond()."""
    deadline = time.monotonic() + timeout
    result = {"ok": False}
    def poll():
        if cond():
            result["ok"] = True
            root.quit()
        elif time.monotonic() > deadline:
            root.quit()
        else:
            root.after(50, poll)
    root.after(0, poll)
    root.mainloop()
    return result["ok"]


def wrap_reload(panel, log):
    """Record each re-read of a panel from config (ProfileView: load_profile)."""
    name = "load_profile" if type(panel).__name__ == "ProfileView" else "reload"
    orig = getattr(panel, name)
    def rec(*a):
        log.append((type(panel).__name__, threading.get_ident()))
        orig(*a)
    setattr(panel, name, rec)


def file_value(cfg, path):
    with open(cfg.config_file) as f:
        data = json.load(f)
    for part in path.split("."):
        data = data[part]
    return data


def watcher_setup(name):
    """App + window with the watcher callback registered and panel reloads recorded."""
    cfg = new_config(name)
    app = make_app(cfg)
    aw = app.app_window
    cfg.on_change(watcher_callback(app))
    reloads = []
    for p in (aw.profile_view, aw.led_panel, aw.timing_panel, aw.osd_panel):
        wrap_reload(p, reloads)
    app.osd.calls.clear()
    assert aw.timing_panel.widgets["multi_click_ms"].get() == "250"
    return cfg, app, aw, aw.window, reloads


def external_timing_edit(cfg, aw, root, value):
    write_external(cfg, lambda d: d["timing"].__setitem__("multi_click_ms", value))
    entry = aw.timing_panel.widgets["multi_click_ms"]
    # Done when the panel shows it, or (old code) when the callback has run.
    run_until(root, lambda: entry.get() == str(value) or aw._status_text[0] == "Config reloaded")
    run_until(root, lambda: False, timeout=0.2)   # let any queued after() run
    return entry


# (1) Watcher path: osd.set_options and the panel reloads run on the Tk thread
def test_watcher_tk_thread():
    cfg, app, aw, root, reloads = watcher_setup("a1.json")
    try:
        external_timing_edit(cfg, aw, root, 420)
        assert app.osd.calls, "osd.set_options was not called"
        threads = {t for t, _ in app.osd.calls}
        assert threads == {MAIN_THREAD}, f"osd.set_options ran on {threads}, Tk is {MAIN_THREAD}"
        names = {n for n, _ in reloads}
        assert names == {"ProfileView", "LEDPanel", "TimingPanel", "OSDPanel"}, names
        assert {t for _, t in reloads} == {MAIN_THREAD}, reloads
        assert app.gesture.config.multi_click_ms == 420, app.gesture.config.multi_click_ms
        assert aw._status_text[0] == "Config reloaded from file", aw._status_text
    finally:
        root.destroy()


# (2) The Timing entry shows the value written to the file
def test_watcher_timing_entry():
    cfg, app, aw, root, reloads = watcher_setup("a2.json")
    try:
        entry = external_timing_edit(cfg, aw, root, 420)
        assert entry.get() == "420", f"Timing entry = {entry.get()!r}"
    finally:
        root.destroy()


# (3) Save after the external edit keeps the file's value (no overwrite with old screen values)
def test_watcher_then_save():
    cfg, app, aw, root, reloads = watcher_setup("a3.json")
    try:
        external_timing_edit(cfg, aw, root, 420)
        aw._save()
        v = file_value(cfg, "timing.multi_click_ms")
        assert v == 420, f"file has multi_click_ms={v} after Save, want 420"
    finally:
        root.destroy()


# (4) GUI Save (and the LED live apply) does not re-read the panels
def test_gui_save_keeps_inputs():
    cfg = new_config("b.json")
    app = make_app(cfg)
    aw, root = app.app_window, app.app_window.window
    try:
        cfg.on_change(watcher_callback(app))
        reloads = []
        for p in (aw.profile_view, aw.led_panel, aw.timing_panel, aw.osd_panel):
            wrap_reload(p, reloads)
        led_entry = aw.led_panel.widgets["fade_delay"]
        timing_entry = aw.timing_panel.widgets["long_press_ms"]
        timing_entry.delete(0, "end"); timing_entry.insert(0, "650")
        aw._save()
        # An unsaved LED edit, then Test LED (live apply): the input must stay.
        led_entry.delete(0, "end"); led_entry.insert(0, "2.5")
        aw._apply_live()
        # Give the watcher (1 s poll) time to see our own save, if it would.
        run_until(root, lambda: False, timeout=1.6)
        assert reloads == [], f"panels reloaded on GUI save: {reloads}"
        assert timing_entry.get() == "650" and led_entry.get() == "2.5", \
            (timing_entry.get(), led_entry.get())
        assert file_value(cfg, "timing.long_press_ms") == 650
    finally:
        root.destroy()


# (5) The selected profile is removed in the file -> 'default' is selected
def test_selected_profile_removed():
    cfg = new_config("c.json")
    app = make_app(cfg)
    aw, root = app.app_window, app.app_window.window
    try:
        cfg.on_change(watcher_callback(app))
        cfg.add_profile("notepad.exe")
        cfg.config["profiles"]["notepad.exe"] = {"press": {"action": "play_pause"}}
        cfg.save()
        aw._refresh_profile_list()
        aw._select_profile("notepad.exe")
        assert aw.profile_view.action_editors["press"].current_action == "play_pause"

        write_external(cfg, lambda d: d["profiles"].pop("notepad.exe"))
        ok = run_until(root, lambda: aw.selected_profile == "default")
        assert ok, f"selected = {aw.selected_profile!r}"
        assert aw.profile_view.current_profile == "default", aw.profile_view.current_profile
        assert "notepad.exe" not in aw.profile_buttons, list(aw.profile_buttons)
        assert aw.profile_view.action_editors["press"].current_action == "mute"
    finally:
        root.destroy()


# (5b) The selected profile still exists -> it stays selected, with the file's bindings
def test_selected_profile_kept():
    cfg = new_config("d.json")
    app = make_app(cfg)
    aw, root = app.app_window, app.app_window.window
    try:
        cfg.on_change(watcher_callback(app))
        cfg.add_profile("notepad.exe")
        cfg.save()
        aw._refresh_profile_list()
        aw._select_profile("notepad.exe")
        write_external(cfg, lambda d: d["profiles"]["notepad.exe"].__setitem__(
            "press", {"action": "play_pause"}))
        ed = aw.profile_view.action_editors["press"]
        ok = run_until(root, lambda: ed.current_action == "play_pause")
        assert ok, ed.current_action
        assert aw.selected_profile == "notepad.exe", aw.selected_profile
    finally:
        root.destroy()


# (6) Before the window exists the change is applied at once (no Tk to touch)
def test_before_window():
    cfg = new_config("e.json")
    app = make_app(cfg, with_window=False)
    write_external(cfg, lambda d: d["timing"].__setitem__("long_press_ms", 700))
    cfg.reload()
    t = threading.Thread(target=watcher_callback(app)); t.start(); t.join(5)
    assert app.gesture.config.long_press_ms == 700, app.gesture.config.long_press_ms
    assert len(app.osd.calls) == 1, app.osd.calls
    # An AppWindow made but not yet created (window None) behaves the same.
    app.app_window = AppWindow(cfg, on_config_change=app._on_config_change)
    t = threading.Thread(target=watcher_callback(app)); t.start(); t.join(5)
    assert len(app.osd.calls) == 2, app.osd.calls


# (7) After the window is gone (quitting) the watcher callback does not raise
def test_after_window_destroyed():
    cfg = new_config("f.json")
    app = make_app(cfg)
    app.app_window.window.destroy()
    errors = []
    def run():
        try:
            watcher_callback(app)()
        except Exception as e:
            errors.append(e)
    t = threading.Thread(target=run); t.start(); t.join(5)
    assert not t.is_alive() and not errors, errors


try:
    check("(1) watcher: OSD + panel reloads on the Tk thread", test_watcher_tk_thread)
    check("(2) watcher: Timing entry shows the file value", test_watcher_timing_entry)
    check("(3) watcher then Save: file keeps the new value", test_watcher_then_save)
    check("(4) GUI Save / Test LED: panels not re-read", test_gui_save_keeps_inputs)
    check("(5) selected profile removed in file -> default", test_selected_profile_removed)
    check("(5b) selected profile kept, shows the file's bindings", test_selected_profile_kept)
    check("(6) before the window exists: applied at once", test_before_window)
    check("(7) window destroyed: watcher callback does not raise", test_after_window_destroyed)
finally:
    shutil.rmtree(TMP_DIR, ignore_errors=True)

print()
if fails:
    print(f"FAILED {len(fails)}: {fails}"); sys.exit(1)
print("ALL CONFIG RELOAD TESTS PASSED")
