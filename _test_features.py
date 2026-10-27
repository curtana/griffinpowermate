"""Tests for new features: macro action, debug panel, startup module."""
import sys, time, threading, traceback

fails = []
def check(name, fn):
    print(f"... {name}", flush=True)
    try:
        fn(); print(f"[PASS] {name}", flush=True)
    except Exception as e:
        fails.append(name); print(f"[FAIL] {name}: {e}"); traceback.print_exc()

def watchdog():
    time.sleep(30); print("[WATCHDOG] timeout"); import os; os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()


def test_startup_module():
    import startup
    assert isinstance(startup.is_enabled(), bool)
    cmd = startup._launch_command()
    assert isinstance(cmd, str) and len(cmd) > 0, cmd
    print("    launch command:", cmd)


def test_macro_action_executor():
    # Ensure the executor handles a macro config without error (empty + populated).
    from actions import ActionExecutor
    from audio import AudioController
    ex = ActionExecutor(AudioController())
    ex.execute({"action": "macro", "sequence": []})        # no-op, must not raise
    # Don't actually send keys in CI-ish run; just confirm dispatch path exists.
    assert hasattr(ex, "_macro")


def _scroll_executor():
    """ActionExecutor with user32.mouse_event replaced by a recorder.
    If audio/keyboard deps aren't installed, they are stubbed only for this import."""
    import types
    stubbed = []
    try:
        import actions
    except ImportError:
        for name in ("audio", "keyboard"):
            try:
                __import__(name)
            except ImportError:
                mod = types.ModuleType(name)
                mod.AudioController = object
                sys.modules[name] = mod; stubbed.append(name)
        sys.modules.pop("actions", None)
        import actions
        for name in stubbed:
            del sys.modules[name]
        sys.modules.pop("actions", None)  # don't leak the stub-bound module
        print("    (stubbed for import:", stubbed, ")")
    calls = []
    actions._mouse_event = lambda flags, dx, dy, data, extra: calls.append((flags, data))
    return actions, actions.ActionExecutor(audio=None), calls


def test_scroll_amount3_is_one_notch():
    actions, ex, calls = _scroll_executor()
    ex.execute({"action": "scroll_down", "amount": 3}, 1)
    assert calls == [(actions.MOUSEEVENTF_WHEEL, -120)], calls
    calls.clear()
    ex.execute({"action": "scroll_up", "amount": 3}, 2)   # 2 detents -> 2 notches
    assert calls == [(actions.MOUSEEVENTF_WHEEL, 240)], calls


def test_scroll_lines_and_fraction_carry():
    actions, ex, calls = _scroll_executor()
    for _ in range(3):
        ex.execute({"action": "scroll_down", "amount": 1}, 1)
    assert calls == [(actions.MOUSEEVENTF_WHEEL, -40)] * 3, calls
    # 0.01 line = 0.4 delta: nothing sent until the carry reaches a whole unit.
    calls.clear()
    for _ in range(10):
        ex.execute({"action": "scroll_down", "amount": 0.01}, 1)
    assert sum(d for _, d in calls) == -4 and all(d == -1 for _, d in calls), calls
    # 0.5 line = 20 delta exactly; total over 6 calls is one notch.
    calls.clear()
    for _ in range(6):
        ex.execute({"action": "scroll_down", "amount": 0.5}, 1)
    assert sum(d for _, d in calls) == -120, calls
    # Direction change: leftover stays within half a unit, signs follow direction.
    calls.clear()
    ex.execute({"action": "scroll_down", "amount": 0.0125}, 1)   # -0.5 -> carried
    ex.execute({"action": "scroll_up", "amount": 3}, 1)
    assert calls and all(d > 0 for _, d in calls) and abs(ex._scroll_remainder) <= 0.5,         (calls, ex._scroll_remainder)
    assert sum(d for _, d in calls) in (119, 120), calls


def test_scroll_has_no_pause():
    actions, ex, calls = _scroll_executor()
    t0 = time.perf_counter()
    for _ in range(100):
        ex.execute({"action": "scroll_down", "amount": 3}, 1)
    elapsed = time.perf_counter() - t0
    assert len(calls) == 100 and elapsed < 0.1, (len(calls), elapsed)
    print(f"    100 scrolls in {elapsed*1000:.2f} ms")


def test_macro_editor_roundtrip():
    import customtkinter as ctk
    from ui.action_editor import ActionEditor
    root = ctk.CTk(); root.withdraw()
    ed = ActionEditor(root, "triple_press"); ed.create()
    # Load a macro and read it back.
    ed.load({"action": "macro", "sequence": ["ctrl+c", "alt+tab", "ctrl+v"]})
    assert ed.current_action == "macro", ed.current_action
    out = ed.get_action()
    assert out["action"] == "macro", out
    assert out["sequence"] == ["ctrl+c", "alt+tab", "ctrl+v"], out["sequence"]
    # 'key' action still works.
    ed.load({"action": "key", "keys": "f5"})
    assert ed.get_action() == {"action": "key", "keys": "f5"}, ed.get_action()
    root.destroy()


def test_triple_and_long_bindable_and_debug_panel():
    from config import Config
    from ui.app_window import AppWindow
    cfg = Config("config.features_test.json")
    aw = AppWindow(cfg, on_config_change=lambda: None, on_quit=lambda: None)
    root = aw.create(); root.update()
    # All 8 triggers present, including triple_press and long_press.
    trig = set(aw.profile_view.action_editors.keys())
    assert {"triple_press", "long_press"} <= trig, trig
    # Bind a macro to long_press and a key to triple_press, save, verify in config.
    aw.profile_view.action_editors["long_press"].load(
        {"action": "macro", "sequence": ["win+d", "ctrl+a"]})
    aw.profile_view.action_editors["triple_press"].load(
        {"action": "key", "keys": "ctrl+shift+n"})
    aw.profile_view.save()
    raw = cfg.get_profile_raw("default")
    assert raw["long_press"]["action"] == "macro", raw.get("long_press")
    assert raw["long_press"]["sequence"] == ["win+d", "ctrl+a"], raw["long_press"]
    assert raw["triple_press"] == {"action": "key", "keys": "ctrl+shift+n"}, raw["triple_press"]
    # Debug panel accepts events.
    aw.log_event("rotate_right +1 -> volume_up"); root.update()
    assert aw.debug_box is not None
    root.destroy()
    import os
    os.remove("config.features_test.json")


for n, f in list(globals().items()):
    if n.startswith("test_") and callable(f):
        check(n, f)

print("\n" + ("ALL FEATURE TESTS PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
