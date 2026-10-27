"""Unit tests for the gesture state machine (no hardware needed)."""
import time
import gesture
from gesture import GestureDetector, GestureConfig, Trigger

fails = []

def run(name, fn):
    try:
        fn()
        print(f"[PASS] {name}")
    except AssertionError as e:
        fails.append(name)
        print(f"[FAIL] {name}: {e}")


def make(multi=120, long=300):
    events = []
    cfg = GestureConfig(multi_click_ms=multi, long_press_ms=long)
    g = GestureDetector(lambda t, a: events.append((t, a)), cfg)
    return g, events


def test_rotate_right():
    g, ev = make()
    g.on_knob(1)
    assert ev == [(Trigger.ROTATE_RIGHT, 1)], ev

def test_rotate_left():
    g, ev = make()
    g.on_knob(-1)
    assert ev == [(Trigger.ROTATE_LEFT, 1)], ev

def test_single_press():
    g, ev = make(multi=80)
    g.on_button(True); g.on_button(False)
    g.update()  # too soon
    assert ev == [], f"fired too early: {ev}"
    time.sleep(0.12)
    g.update()
    assert ev == [(Trigger.PRESS, 0)], ev

def test_double_press():
    g, ev = make(multi=100)
    g.on_button(True); g.on_button(False)
    g.on_button(True); g.on_button(False)
    time.sleep(0.13)
    g.update()
    assert ev == [(Trigger.DOUBLE_PRESS, 0)], ev

def test_triple_press():
    g, ev = make(multi=100)
    for _ in range(3):
        g.on_button(True); g.on_button(False)
    time.sleep(0.13)
    g.update()
    assert ev == [(Trigger.TRIPLE_PRESS, 0)], ev

def test_long_press():
    g, ev = make(long=100)
    g.on_button(True)
    time.sleep(0.13)
    g.update()
    assert ev == [(Trigger.LONG_PRESS, 0)], ev
    # Release should NOT produce a spurious single press.
    g.on_button(False)
    time.sleep(0.13)
    g.update()
    assert ev == [(Trigger.LONG_PRESS, 0)], f"spurious event after long press: {ev}"

def test_press_rotate_right():
    g, ev = make()
    g.on_button(True)
    g.on_knob(1)
    assert ev == [(Trigger.PRESS_ROTATE_RIGHT, 1)], ev

def test_press_rotate_no_spurious_press():
    # The key regression: rotating while held must NOT also fire a single press.
    g, ev = make(multi=80, long=300)
    g.on_button(True)
    g.on_knob(1)          # press+rotate
    g.on_button(False)    # release
    time.sleep(0.12)
    g.update()
    assert ev == [(Trigger.PRESS_ROTATE_RIGHT, 1)], f"spurious press: {ev}"

def test_long_press_not_fired_while_rotating():
    # Holding and rotating past long_press_ms should not trigger long_press.
    g, ev = make(long=100)
    g.on_button(True)
    g.on_knob(1)          # now pressed_and_rotating
    time.sleep(0.13)
    g.update()
    assert all(t != Trigger.LONG_PRESS for t, _ in ev), f"unexpected long press: {ev}"


# --- Rotation sensitivity / acceleration (time injected via on_knob(now=...)) ---

def make_rot(sens=1.0, accel=0.0):
    events = []
    cfg = GestureConfig(rotate_sensitivity=sens, rotate_acceleration=accel)
    g = GestureDetector(lambda t, a: events.append((t, a)), cfg)
    return g, events

def spin(g, n, dt, delta=1, t0=100.0):
    """n knob reports of `delta`, `dt` seconds apart; returns the last time used."""
    t = t0
    for _ in range(n):
        g.on_knob(delta, now=t)
        t += dt
    return t - dt

def total(ev):
    return sum(a if t == Trigger.ROTATE_RIGHT else -a for t, a in ev)

def test_accel_off_is_identity_when_fast():
    # (1) a=0: even very fast spinning (dt=0, 1ms) fires exactly the raw deltas.
    g, ev = make_rot(accel=0.0)
    spin(g, 20, 0.0)
    spin(g, 20, 0.001, t0=200.0)
    assert ev == [(Trigger.ROTATE_RIGHT, 1)] * 40, ev

def test_accel_slow_is_identity():
    # (2) a=1 at ~3.6 detents/s (< v0=4): multiplier stays 1.
    g, ev = make_rot(accel=1.0)
    spin(g, 20, 0.28)
    assert ev == [(Trigger.ROTATE_RIGHT, 1)] * 20, ev
    assert g.rotate_speed <= gesture.ACCEL_V0, g.rotate_speed

def test_accel_fast_boosts_within_gmax():
    # (3) a=1 at 100 detents/s (> vmax): multiplier > 1 but never above gmax (=10).
    g, ev = make_rot(accel=1.0)
    t = spin(g, 30, 0.01)
    gmax = 1 + gesture.ACCEL_GAIN * 1.0
    m = g._accel_multiplier(1, t + 0.01)
    assert 1.0 < m <= gmax, m
    assert total(ev) > 30, total(ev)
    # each report adds at most gmax (dt=0 included: the dt floor keeps it bounded)
    g2, ev2 = make_rot(accel=1.0)
    spin(g2, 50, 0.0)
    assert max(a for _, a in ev2) <= gmax and total(ev2) <= 50 * gmax, ev2

def test_accel_reverse_fast_boosts_left():
    g, ev = make_rot(accel=1.0)
    spin(g, 30, 0.01, delta=-1)
    assert all(t == Trigger.ROTATE_LEFT for t, _ in ev) and total(ev) < -30, ev

def test_accel_resets_after_pause():
    # (4) after a pause > 0.3s the first report is precision mode (multiplier 1).
    g, ev = make_rot(accel=1.0)
    t = spin(g, 30, 0.01)
    assert g.rotate_speed > gesture.ACCEL_VMAX
    ev.clear()
    g.accumulated_rotation = 0.0
    g.on_knob(1, now=t + 0.31)
    assert ev == [(Trigger.ROTATE_RIGHT, 1)], ev
    assert g.rotate_speed == 0.0, g.rotate_speed

def test_sensitivity_applied():
    # (5) sensitivity 2.0 doubles; 0.5 needs two detents per event.
    g, ev = make_rot(sens=2.0)
    spin(g, 3, 0.5)
    assert ev == [(Trigger.ROTATE_RIGHT, 2)] * 3, ev
    g, ev = make_rot(sens=0.5)
    spin(g, 4, 0.5, delta=-1)
    assert ev == [(Trigger.ROTATE_LEFT, 1)] * 2, ev

def test_zero_delta_keeps_timing():
    # Button-only reports (delta 0) must not reset rotation speed tracking.
    g, ev = make_rot(accel=1.0)
    t = spin(g, 30, 0.01)
    speed = g.rotate_speed
    g.on_knob(0, now=t + 5.0)
    assert g.rotate_speed == speed and g.last_rotate_time == t and ev, (g.rotate_speed, speed)

def test_config_change_applies_live():
    # main._on_config_change mutates the shared GestureConfig; next report uses it.
    g, ev = make_rot(accel=0.0)
    t = spin(g, 30, 0.01)
    n = total(ev)
    g.config.rotate_acceleration = 1.0
    g.on_knob(1, now=t + 0.01)
    assert total(ev) - n > 1, ev[-1]


for n, f in list(globals().items()):
    if n.startswith("test_") and callable(f):
        run(n, f)

print("\n" + ("ALL GESTURE TESTS PASSED" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
