"""Headless-ish smoke test: build the GUI, exercise it, then close.

Run: python _smoketest_gui.py
Exits 0 on success, non-zero on any exception during construction/interaction.
"""
import sys
import traceback

results = []

def check(name, fn):
    print(f"... {name}", flush=True)
    try:
        fn()
        results.append((name, True, ""))
        print(f"[PASS] {name}", flush=True)
    except Exception as e:
        results.append((name, False, f"{e}\n{traceback.format_exc()}"))
        print(f"[FAIL] {name}: {e}", flush=True)


def _watchdog():
    import time, os
    time.sleep(30)
    print("[WATCHDOG] timeout — forcing exit", flush=True)
    os._exit(3)


def main():
    import threading
    threading.Thread(target=_watchdog, daemon=True).start()

    from config import Config
    from ui.app_window import AppWindow

    # Use a temp config file so we don't clobber the real one.
    cfg = Config("config.smoketest.json")

    win_holder = {}

    def build():
        aw = AppWindow(cfg, on_config_change=lambda: None)
        root = aw.create()
        win_holder["aw"] = aw
        win_holder["root"] = root

    check("build_window", build)

    aw = win_holder.get("aw")
    root = win_holder.get("root")

    if aw and root:
        # Pump events so widgets realize/layout.
        check("update_idletasks", lambda: root.update())

        # Exercise: default profile must be auto-selected and load 8 trigger rows.
        def check_bindings_loaded():
            assert aw.selected_profile == "default", aw.selected_profile
            n = len(aw.profile_view.action_editors)
            assert n == 8, f"expected 8 trigger editors, got {n}"
            # The default 'press' binding should have loaded as 'mute'.
            ed = aw.profile_view.action_editors["press"]
            assert ed.current_action == "mute", f"press action = {ed.current_action}"
        check("bindings_loaded", check_bindings_loaded)

        # Regression (#19): right after create(), with no clicks, every trigger
        # row is drawn with the default profile's action, no row is inflated by
        # an empty parameter frame, and press / double_press sit inside the
        # visible part of the scroll area.
        def check_bindings_rows_visible():
            root.update_idletasks()
            pv = aw.profile_view
            want = cfg.get_profile_raw("default")
            canvas_h = pv.frame._parent_canvas.winfo_height()
            for trigger, ed in pv.action_editors.items():
                assert ed.winfo_ismapped(), f"{trigger} row not mapped"
                expected = want.get(trigger, {}).get("action", "none")
                assert ed.action_dropdown.get() == expected, \
                    f"{trigger} dropdown = {ed.action_dropdown.get()!r}, want {expected!r}"
                # A row is as tall as its tallest widget (dropdown or entry).
                assert ed.winfo_height() <= 2 * ed.action_dropdown.winfo_height(), \
                    f"{trigger} row is {ed.winfo_height()}px tall"
            for trigger in ("press", "double_press"):
                ed = pv.action_editors[trigger]
                bottom = ed.winfo_y() + ed.winfo_height()
                assert bottom <= canvas_h, \
                    f"{trigger} row ends at y={bottom}, visible area is {canvas_h}px"
        check("bindings_rows_visible", check_bindings_rows_visible)

        # Changing the dropdown shows / hides the parameter frame again.
        def check_param_frame_toggle():
            ed = aw.profile_view.action_editors["press"]
            ed._on_action_changed("volume_up")
            root.update_idletasks()
            assert ed.param_frame.winfo_ismapped(), "param frame hidden for volume_up"
            assert "amount" in ed.param_widgets
            ed._on_action_changed("mute")
            root.update_idletasks()
            assert not ed.param_frame.winfo_ismapped(), "empty param frame still shown"
            assert ed.winfo_height() <= 2 * ed.action_dropdown.winfo_height(), ed.winfo_height()
            aw._select_profile("default")
        check("param_frame_toggle", check_param_frame_toggle)

        # Exercise: round-trip a binding edit through save -> raw config.
        def check_save_roundtrip():
            ed = aw.profile_view.action_editors["rotate_right"]
            # rotate_right default is volume_up; verify it loaded and saves back.
            assert ed.current_action == "volume_up", ed.current_action
            aw.profile_view.save()
            raw = cfg.get_profile_raw("default")
            assert raw["rotate_right"]["action"] == "volume_up", raw.get("rotate_right")
        check("save_roundtrip", check_save_roundtrip)

        # Exercise: LED + timing tabs populated their widgets (proves self.pack ran).
        def check_panels():
            assert "mode" in aw.led_panel.widgets
            assert "multi_click_ms" in aw.timing_panel.widgets
            # reload() uses "end" index — would raise if ctk.END bug remained.
            aw.led_panel.reload()
            aw.timing_panel.reload()
        check("panels_populated", check_panels)

        # Exercise: inheritance — add an app profile overriding only rotate_right.
        def check_inheritance():
            cfg.add_profile("notepad.exe")
            cfg.config["profiles"]["notepad.exe"] = {
                "rotate_right": {"action": "scroll_down", "amount": 5}
            }
            eff = cfg.get_profile("notepad.exe")
            assert eff["rotate_right"]["action"] == "scroll_down", eff["rotate_right"]
            # press not overridden -> inherited from default (mute)
            assert eff["press"]["action"] == "mute", eff.get("press")
        check("profile_inheritance", check_inheritance)

        # Thread-safe status update (schedules via after()).
        check("set_status", lambda: aw.set_status("Test", "green"))
        check("pump_after", lambda: root.update())

        # Tear down.
        check("destroy", lambda: root.destroy())

    # Report
    print("\n=== GUI SMOKE TEST RESULTS ===")
    ok = True
    for name, passed, err in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed:
            ok = False
            print("   " + err.replace("\n", "\n   "))
    print("==============================")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
