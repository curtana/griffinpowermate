"""Tests for config validation (issue #8): a bad value in config.json or in a
GUI field must not stop the LED thread, the app, Save, or send the wrong key.
Bad values fall back to defaults in memory (logged); config.json is untouched."""
import os, sys, time, json, threading, traceback, tempfile, shutil, logging, types

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

TMP = tempfile.mkdtemp(prefix="pm_cfg_")


class _Warnings(logging.Handler):
    """Collect WARNING+ messages from the config logger during a block."""
    def __enter__(self):
        logging.Handler.__init__(self, logging.WARNING)
        self.messages = []
        self.log = logging.getLogger("config")
        self.log.addHandler(self)
        return self
    def emit(self, record):
        self.messages.append(record.getMessage())
    def __exit__(self, *exc):
        self.log.removeHandler(self)
    def has(self, path):
        return any(m.startswith(f"config: {path} ") for m in self.messages)


_n = [0]
def _config_file(data):
    """Write `data` (dict, or raw text) to a fresh temp config.json; return its path."""
    _n[0] += 1
    path = os.path.join(TMP, f"config_{_n[0]}.json")
    with open(path, "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, indent=2))
    return path


def _load(data):
    from config import Config
    return Config(_config_file(data))


# --- (1) each rule: invalid value -> default + warning --------------------

def test_invalid_values_use_defaults():
    from config import DEFAULT_CONFIG
    bad_timing = {"multi_click_ms": 10, "long_press_ms": "abc",
                  "rotate_sensitivity": 0, "rotate_acceleration": None}
    bad_led = {"mode": "rainbow", "brightness": 1.5, "pulse_speed": 600,
               "pulse_table": 3, "fade_speed": 0, "fade_delay": -1}
    with _Warnings() as w:
        cfg = _load({"timing": bad_timing, "led": bad_led})
    for key in bad_timing:
        assert cfg.get(f"timing.{key}") == DEFAULT_CONFIG["timing"][key], key
        assert w.has(f"timing.{key}"), (key, w.messages)
    for key in bad_led:
        assert cfg.get(f"led.{key}") == DEFAULT_CONFIG["led"][key], key
        assert w.has(f"led.{key}"), (key, w.messages)
    # Message format from the work order.
    assert "config: timing.multi_click_ms = 10 invalid, using 250" in w.messages, w.messages


def test_range_edges():
    from config import Config
    cases = [("timing.multi_click_ms", 50, 2000, 49, 2001),
             ("timing.long_press_ms", 100, 5000, 99, 5001),
             ("timing.rotate_sensitivity", 0.01, 10, 0, 10.5),
             ("timing.rotate_acceleration", 0, 1, -0.1, 1.1),
             ("led.brightness", 0, 1, -0.1, 1.1),
             ("led.pulse_speed", 0, 510, -1, 511),
             ("led.pulse_table", 0, 2, -1, 3),
             ("led.flash_on_ms", 0, 10 ** 6, -1, None),
             ("led.fade_speed", 0.01, 1000, 0, None),
             ("led.fade_delay", 0, 1000, -0.5, None)]
    cfg = _load({})
    for path, lo, hi, below, above in cases:
        assert cfg.set(path, lo) and cfg.get(path) == lo, (path, lo)
        assert cfg.set(path, hi) and cfg.get(path) == hi, (path, hi)
        for bad in (below, above, True, "x", float("nan")):
            if bad is None:
                continue
            with _Warnings() as w:
                assert not cfg.set(path, bad), (path, bad)
            assert w.has(path), (path, bad, w.messages)


def test_flash_pair_zero():
    with _Warnings() as w:
        cfg = _load({"led": {"flash_on_ms": 0, "flash_off_ms": 0}})
    assert (cfg.get("led.flash_on_ms"), cfg.get("led.flash_off_ms")) == (200, 800)
    assert any("flash_on_ms + led.flash_off_ms" in m for m in w.messages), w.messages
    # One side zero is fine (always on / always off).
    cfg = _load({"led": {"flash_on_ms": 0, "flash_off_ms": 500}})
    assert (cfg.get("led.flash_on_ms"), cfg.get("led.flash_off_ms")) == (0, 500)
    # GUI path: going 200/0 -> 0/500 must not trip on the 0/0 in between.
    cfg = _load({"led": {"flash_on_ms": 200, "flash_off_ms": 0}})
    cfg.set("led.flash_on_ms", 0); cfg.set("led.flash_off_ms", 500)
    assert cfg.check_flash_pair()
    assert (cfg.get("led.flash_on_ms"), cfg.get("led.flash_off_ms")) == (0, 500)
    cfg.set("led.flash_off_ms", 0)
    assert not cfg.check_flash_pair()
    assert (cfg.get("led.flash_on_ms"), cfg.get("led.flash_off_ms")) == (200, 800)


def test_profile_actions():
    with _Warnings() as w:
        cfg = _load({"profiles": {"default": {
            "rotate_right": {"action": "volume_up", "amount": "lots"},
            "rotate_left": {"action": "volume_down", "amount": "0.02"},
            "press_rotate_right": {"action": "scroll_down", "amount": None},
            "press": {"action": "key", "keys": 1},
            "double_press": {"action": "key", "keys": None},
            "triple_press": {"action": "macro", "sequence": "ctrl+c"},
            "long_press": {"action": "macro", "sequence": ["a", 2, None]},
        }}})
    p = cfg.get_profile("default")
    assert p["rotate_right"]["amount"] == 0.04 and w.has("profiles.default.rotate_right.amount")
    assert p["rotate_left"]["amount"] == 0.02
    assert p["press_rotate_right"]["amount"] == 3 and w.has("profiles.default.press_rotate_right.amount")
    assert p["press"]["keys"] == "1", p["press"]
    assert p["double_press"]["keys"] == "" and w.has("profiles.default.double_press.keys")
    assert p["triple_press"]["sequence"] == ["ctrl+c"]
    assert p["long_press"]["sequence"] == ["a", "2"] and w.has("profiles.default.long_press.sequence")


# --- (2) valid values / numeric strings ------------------------------------

def test_valid_values_unchanged():
    from config import DEFAULT_CONFIG, sanitize
    with _Warnings() as w:
        cleaned = sanitize(DEFAULT_CONFIG)
    assert cleaned == DEFAULT_CONFIG and not w.messages, w.messages
    with _Warnings() as w:
        cfg = _load(DEFAULT_CONFIG)
    assert cfg.config == DEFAULT_CONFIG and not w.messages, w.messages


def test_numeric_strings_converted():
    with _Warnings() as w:
        cfg = _load({"timing": {"multi_click_ms": "300", "rotate_sensitivity": "2.5"},
                     "led": {"brightness": "0.5", "pulse_table": "1"}})
    assert cfg.get("timing.multi_click_ms") == 300
    assert cfg.get("timing.rotate_sensitivity") == 2.5
    assert cfg.get("led.brightness") == 0.5 and cfg.get("led.pulse_table") == 1
    assert not w.messages, w.messages


def test_inheritance_untouched():
    data = {"profiles": {"default": {"press": {"action": "mute"}},
                         "app.exe": {"press": {"action": "none"}, "long_press": {}}}}
    cfg = _load(data)
    assert cfg.get_profile_raw("app.exe") == {"press": {"action": "none"}, "long_press": {}}
    assert cfg.get_profile("app.exe")["press"] == {"action": "none"}


def test_osd_and_unknown_keys_pass_through():
    cfg = _load({"osd": {"opacity": 7}, "extra": {"x": 1}, "led": {"future": "y"}})
    assert cfg.get("osd.opacity") == 7          # ui/osd.py set_options clamps it
    assert cfg.get("extra.x") == 1 and cfg.get("led.future") == "y"


# --- (3) LED loop survives bad values -------------------------------------

class _FakeDevice:
    def __init__(self):
        self.levels = []
    def set_brightness(self, v):
        self.levels.append(v)
    def set_pulse_awake(self, on):
        pass
    def set_pulse_mode(self, table, speed):
        pass


def _run_led(led, seconds=0.2):
    """Run the real LED thread briefly; return the exceptions it raised."""
    errors = []
    old_hook = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)
    try:
        led.start()
        time.sleep(seconds)
        alive = led.thread.is_alive()
        led.stop()
    finally:
        threading.excepthook = old_hook
    assert alive, f"LED thread died: {errors!r}"
    assert not errors, errors
    assert led.device.levels, "LED loop never wrote a brightness"


def _led_from_config(data):
    """Build an LEDController and set it up the way main.py does."""
    import main
    from led import LEDController
    app = types.SimpleNamespace(config=_load(data),
                                led=LEDController(_FakeDevice(), lambda: 0.5))
    main.PowerMateApp._setup_led_from_config(app)
    return app.led


def test_led_fade_speed_zero():
    _run_led(_led_from_config({"led": {"mode": "volume", "fade_speed": 0, "fade_delay": 0}}))


def test_led_flash_zero_zero():
    _run_led(_led_from_config({"led": {"mode": "flash", "flash_on_ms": 0, "flash_off_ms": 0}}))


def test_led_mode_rainbow():
    _run_led(_led_from_config({"led": {"mode": "rainbow"}}))


def test_led_loop_defends_itself():
    # Values set directly on the controller (bypassing Config) must not kill it.
    from led import LEDController
    led = LEDController(_FakeDevice(), lambda: 0.5)
    led.fade_speed, led.fade_delay = 0, 0
    _run_led(led, 0.1)
    led = LEDController(_FakeDevice(), lambda: 0.5)
    led.mode, led.flash_on_ms, led.flash_off_ms = "flash", 0, 0
    _run_led(led, 0.1)
    led = LEDController(_FakeDevice(), lambda: 0.5)
    led.mode = "rainbow"
    _run_led(led, 0.1)
    # An exception in one pass is logged and the loop keeps going.
    calls = []
    def flaky_volume():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("audio hiccup")
        return 0.5
    led = LEDController(_FakeDevice(), flaky_volume)
    _run_led(led, 0.2)
    assert len(calls) >= 2, calls


# --- (4) rotate_acceleration null -----------------------------------------

def test_gesture_acceleration_null():
    from gesture import GestureDetector, GestureConfig, Trigger
    fired = []
    cfg = _load({"timing": {"rotate_acceleration": None}})
    gc = GestureConfig(rotate_acceleration=cfg.get("timing.rotate_acceleration", 0.0))
    g = GestureDetector(lambda t, a: fired.append((t, a)), gc)
    g.on_knob(1, now=1.0); g.on_knob(1, now=1.02)
    assert fired == [(Trigger.ROTATE_RIGHT, 1)] * 2, fired
    # Even if a None reaches the detector directly (e.g. set by hand).
    fired.clear()
    g = GestureDetector(lambda t, a: fired.append((t, a)),
                        GestureConfig(rotate_acceleration=None))
    g.on_knob(1, now=1.0); g.on_knob(1, now=1.02); g.on_knob(-1, now=1.04)
    assert fired == [(Trigger.ROTATE_RIGHT, 1)] * 2 + [(Trigger.ROTATE_LEFT, 1)], fired


# --- (5)(6) GUI: shortcut "1", invalid entries -----------------------------

_gui = {}
def _window():
    """One real AppWindow on a temp config, shared by the GUI tests."""
    if "aw" not in _gui:
        from config import DEFAULT_CONFIG
        from ui.app_window import AppWindow
        cfg = _load(DEFAULT_CONFIG)
        changes = []
        aw = AppWindow(cfg, on_config_change=lambda: changes.append(1))
        root = aw.create()
        root.update()
        _gui.update(aw=aw, root=root, cfg=cfg)
    return _gui["aw"], _gui["root"], _gui["cfg"]


def _set_entry(entry, text):
    entry.delete(0, "end"); entry.insert(0, text)


def test_shortcut_digit_stays_text():
    aw, root, cfg = _window()
    ed = aw.profile_view.action_editors["press"]
    ed.load({"action": "key", "keys": "1"})
    assert ed.get_action() == {"action": "key", "keys": "1"}, ed.get_action()
    ed.load({"action": "launch", "path": "123"})
    assert ed.get_action() == {"action": "launch", "path": "123"}
    ed.load({"action": "volume_up", "amount": "0.1"})
    assert ed.get_action() == {"action": "volume_up", "amount": 0.1}
    _set_entry(ed.param_widgets["amount"], "lots")
    assert ed.get_action() == {"action": "volume_up", "amount": 0.04}
    assert ed.invalid_params == ["amount"]


def test_save_with_invalid_entries():
    aw, root, cfg = _window()
    aw.timing_panel.reload(); aw.led_panel.reload()
    _set_entry(aw.timing_panel.widgets["multi_click_ms"], "abc")     # not a number
    _set_entry(aw.timing_panel.widgets["long_press_ms"], "700")      # valid
    _set_entry(aw.led_panel.widgets["fade_speed"], "0")              # out of range
    _set_entry(aw.led_panel.widgets["fade_delay"], "2.5")            # valid
    ed = aw.profile_view.action_editors["press"]
    ed.load({"action": "key", "keys": "1"})
    ed2 = aw.profile_view.action_editors["rotate_right"]
    ed2.load({"action": "volume_up", "amount": "x"})
    aw._save()
    root.update()
    assert cfg.get("timing.multi_click_ms") == 250       # kept
    assert cfg.get("timing.long_press_ms") == 700        # rest saved
    assert cfg.get("led.fade_speed") == 1.0              # default
    assert cfg.get("led.fade_delay") == 2.5
    with open(cfg.config_file) as f:
        on_disk = json.load(f)
    assert on_disk["timing"]["long_press_ms"] == 700
    assert on_disk["profiles"]["default"]["press"] == {"action": "key", "keys": "1"}
    assert on_disk["profiles"]["default"]["rotate_right"] == {"action": "volume_up", "amount": 0.04}
    text = aw.status_label.cget("text")
    color = aw.status_label.cget("text_color")
    assert color == "orange", (text, color)
    for name in ("timing.multi_click_ms", "led.fade_speed", "rotate_right.amount"):
        assert name in text, text
    assert "long_press_ms" not in text, text
    # A clean save goes back to green.
    aw.timing_panel.reload(); aw.led_panel.reload()
    ed2.load({"action": "volume_up", "amount": 0.04})
    aw._save(); root.update()
    assert aw.status_label.cget("text_color") == "green", aw.status_label.cget("text")


def test_close_window():
    if "root" in _gui:
        _gui["root"].destroy()
        _gui.clear()


# --- (7) config.json is never rewritten by validation ---------------------

def test_file_not_rewritten():
    raw = ('{"timing": {"multi_click_ms": 1, "long_press_ms": "x"},\n'
           ' "led": {"mode": "rainbow", "flash_on_ms": 0, "flash_off_ms": 0},\n'
           ' "profiles": {"default": {"press": {"action": "key", "keys": 1}}}}\n')
    path = _config_file(raw)
    with open(path, "rb") as f:
        before = f.read()
    mtime = os.path.getmtime(path)
    from config import Config
    cfg = Config(path)
    cfg.get("led.mode"); cfg.get_profile("default")
    cfg.reload()
    time.sleep(1.3)   # let the watcher thread take a look too
    with open(path, "rb") as f:
        assert f.read() == before
    assert os.path.getmtime(path) == mtime
    assert cfg.get("led.mode") == "volume"


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        check(name, fn)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
