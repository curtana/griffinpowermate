"""Tests for device.py USB error handling and reconnect (fake USB only — never opens the real knob)."""
import sys, time, threading, traceback, errno, logging, types

fails = []
def check(name, fn):
    print(f"... {name}", flush=True)
    try:
        fn(); print(f"[PASS] {name}", flush=True)
    except Exception as e:
        fails.append(name); print(f"[FAIL] {name}: {e!r}"); traceback.print_exc()

def watchdog():
    time.sleep(30); print("[WATCHDOG] timeout"); import os; os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()

import usb.core
import usb.util
import device
from device import PowerMateDevice


class FakeDev:
    """Stands in for usb.core.Device. `reads` is a list of byte lists or exceptions."""
    def __init__(self, reads=None, write_delay=0.0):
        self.reads = list(reads or [])
        self.write_delay = write_delay
        self.writes = []

    def read(self, ep, size, timeout=None):
        item = self.reads.pop(0) if self.reads else usb.core.USBTimeoutError("Operation timed out", -7, 138)
        if isinstance(item, BaseException):
            raise item
        return item

    def write(self, ep, data, timeout=None):
        time.sleep(self.write_delay)
        self.writes.append(bytes(data))
        return len(data)

    def ctrl_transfer(self, *a, **kw):
        return 0


class patched:
    """Temporarily replace attributes: patched((obj, 'name', value), ...)."""
    def __init__(self, *items):
        self.items = items; self.saved = []
    def __enter__(self):
        for obj, name, val in self.items:
            self.saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        return self
    def __exit__(self, *exc):
        for obj, name, val in reversed(self.saved):
            setattr(obj, name, val)


def _usb_noops(**over):
    """Patch usb.util / get_backend so no real libusb call happens."""
    base = dict(claim_interface=lambda d, i: None, release_interface=lambda d, i: None,
                dispose_resources=lambda d: None)
    base.update(over)
    return [(usb.util, k, v) for k, v in base.items()] + [(device, "get_backend", lambda: None)]


def _dev_with(fake):
    d = PowerMateDevice(); d.dev = fake
    return d


# ① timeout -> no exception, no callback
def test_timeout_is_silent():
    got = []
    timeouts = [
        usb.core.USBTimeoutError("Operation timed out", -7, 138),            # libusb1 on Windows
        usb.core.USBError("Operation timed out", -7, 110),                    # plain USBError, ETIMEDOUT
        usb.core.USBError("[Errno 10060] timeout", None, None),               # message only
    ]
    for exc in timeouts:
        d = _dev_with(FakeDev([exc]))
        d.read_events(lambda b, k: got.append((b, k)), timeout_ms=10)
        assert d.dev is not None, "timeout must not drop the connection"
    assert got == [], got


# ② ENODEV USBError -> propagates
def test_enodev_propagates():
    for exc in (usb.core.USBError("No such device (it may have been disconnected)", -4, errno.ENODEV),
                usb.core.USBError("Input/Output Error", -1, errno.EIO),
                usb.core.USBError("Pipe error", -9, errno.EPIPE)):
        d = _dev_with(FakeDev([exc]))
        try:
            d.read_events(lambda b, k: None, timeout_ms=10)
        except usb.core.USBError as e:
            assert e is exc
        else:
            raise AssertionError(f"read_events swallowed {exc!r}")


# ③ normal 6-byte report -> callback(button, signed delta)
def test_normal_report():
    got = []
    d = _dev_with(FakeDev([[1, 0xFE, 0, 0, 0, 0], [0, 3, 0, 0, 0, 0]]))
    d.read_events(lambda b, k: got.append((b, k)))
    d.read_events(lambda b, k: got.append((b, k)))
    assert got == [(1, -2), (0, 3)], got


# ④ claim_interface failure -> find() False, dev None, resources disposed
def test_claim_failure():
    disposed = []
    def bad_claim(d, i):
        raise usb.core.USBError("Access denied (insufficient permissions)", -3, errno.EACCES)
    fake = FakeDev()
    with patched(*_usb_noops(claim_interface=bad_claim, dispose_resources=lambda d: disposed.append(d)),
                 (usb.core, "find", lambda **kw: fake)):
        d = PowerMateDevice()
        assert d.find() is False, "find() must fail when the interface cannot be claimed"
        assert d.dev is None
        assert disposed == [fake], disposed


def test_find_success_and_absent():
    fake = FakeDev()
    with patched(*_usb_noops(), (usb.core, "find", lambda **kw: fake)):
        d = PowerMateDevice()
        assert d.find() is True and d.dev is fake
    with patched(*_usb_noops(), (usb.core, "find", lambda **kw: None)):
        d = PowerMateDevice()
        assert d.find() is False and d.dev is None


# ⑤ close() racing set_brightness() -> no exception in either thread
def test_close_vs_brightness_race():
    errors = []
    def run(fn):
        try: fn()
        except Exception as e: errors.append(e)

    # (a) close() holds the lock (slow release) while set_brightness() is entered:
    #     the old code checked self.dev before the lock and then wrote to None.
    with patched(*_usb_noops(release_interface=lambda d, i: time.sleep(0.2))):
        d = _dev_with(FakeDev())
        t = threading.Thread(target=run, args=(d.close,)); t.start()
        time.sleep(0.05)
        run(lambda: d.set_brightness(0.5))
        run(lambda: d.set_pulse_awake(True))
        run(lambda: d.set_pulse_mode(0, 255))
        t.join()
        assert d.dev is None

    # (b) a slow write is in progress when close() is called.
    with patched(*_usb_noops()):
        fake = FakeDev(write_delay=0.2)
        d = _dev_with(fake)
        t = threading.Thread(target=run, args=(lambda: d.set_brightness(1.0),)); t.start()
        time.sleep(0.05)
        run(d.close)
        t.join()
        assert d.dev is None and fake.writes == [b"\xff"], fake.writes
        run(lambda: d.set_brightness(0.3))   # after close: silent no-op

    # (c) a write that fails with a non-USB error must not escape into the LED thread.
    d = _dev_with(FakeDev())
    def boom(*a, **k): raise AttributeError("'NoneType' object has no attribute 'write'")
    d.dev.write = boom
    run(lambda: d.set_brightness(0.5))
    assert not errors, errors


# ⑥ worker loop: read error -> close -> immediate re-find -> reconnected
def test_worker_reconnect():
    import logging as _logging
    class _NoFile(_logging.NullHandler):
        def __init__(self, *a, **k): super().__init__()
    with patched((_logging, "FileHandler", _NoFile)):   # don't create powermate.log on import
        import main

    statuses, finds, sleeps, loops = [], [], [], [0]
    first, second = FakeDev([usb.core.USBError("No such device", -4, errno.ENODEV)]), FakeDev()
    # find() results in order: connect, (unplugged) fail, replugged connect
    plan = [first, None, second]

    fake_self = types.SimpleNamespace(running=True, device=PowerMateDevice(),
                                      app_window=types.SimpleNamespace(set_status=lambda t, c: statuses.append(t)),
                                      gesture=types.SimpleNamespace(on_button=lambda p: None,
                                                                    on_knob=lambda k: None, update=lambda: _tick()))

    def _tick():   # safety stop: the old code spun on the dead device forever
        loops[0] += 1
        if loops[0] > 500:
            fake_self.running = False

    def fake_find(**kw):
        dev = plan.pop(0) if plan else second
        finds.append(dev)
        if dev is second:
            fake_self.running = False   # stop after reconnect
        return dev

    def fake_sleep(s):
        sleeps.append((len(finds), s))   # tagged with how many find() calls happened so far

    with patched(*_usb_noops(), (usb.core, "find", fake_find), (main.time, "sleep", fake_sleep)):
        main.PowerMateApp._worker(fake_self)

    assert finds == [first, None, second], finds
    assert fake_self.device.dev is second
    assert statuses == ["PowerMate connected", "Device error", "PowerMate not found", "PowerMate connected"], statuses
    # The absent wait runs in short steps (wait_while_running), so add them up:
    # one ~5-second wait while the device was absent (after find#2, before find#3) ...
    absent = sum(s for n, s in sleeps if n == 2)
    assert abs(absent - 5) < 0.05, sleeps
    # ... and no wait between close and re-find (only the 10 ms loop sleep after find#1)
    after_close = sum(s for n, s in sleeps if n == 1)
    assert after_close <= 0.011, sleeps


def _run_worker_clocked(make_dev, max_finds, stop_at=None):
    """Run PowerMateApp._worker with a fake clock (time.sleep advances it).
    make_dev(i) -> FakeDev returned by the i-th find(). Returns (find_times, end_clock, stop_clock)."""
    import logging as _logging
    class _NoFile(_logging.NullHandler):
        def __init__(self, *a, **k): super().__init__()
    with patched((_logging, "FileHandler", _NoFile)):
        import main

    clock, find_times, loops, stopped = [0.0], [], [0], [None]
    fake_self = types.SimpleNamespace(running=True, device=PowerMateDevice(), app_window=None,
                                      gesture=types.SimpleNamespace(on_button=lambda p: None,
                                                                    on_knob=lambda k: None, update=lambda: _tick()))

    def _tick():
        loops[0] += 1
        if loops[0] > 5000:
            fake_self.running = False

    def fake_find(**kw):
        find_times.append(clock[0])
        if len(find_times) >= max_finds:
            fake_self.running = False
        return make_dev(len(find_times) - 1)

    def fake_sleep(s):
        clock[0] += s
        if stop_at is not None and stopped[0] is None and clock[0] >= stop_at:
            stopped[0] = clock[0]
            fake_self.running = False

    with patched(*_usb_noops(), (usb.core, "find", fake_find), (main.time, "sleep", fake_sleep)):
        main.PowerMateApp._worker(fake_self)
    return find_times, clock[0], stopped[0]


def _eio():
    return usb.core.USBError("Entity not found", -1, errno.EIO)


# (7) device present but read pipe broken: re-find waits grow 0 -> 1 -> 2 -> 4 -> 5 (cap)
def test_worker_backoff():
    times, _, _ = _run_worker_clocked(lambda i: FakeDev([_eio()]), max_finds=7)
    gaps = [b - a for a, b in zip(times, times[1:])]
    expected = [0, 1, 2, 4, 5, 5]
    assert len(gaps) == len(expected), (times, gaps)
    for g, e in zip(gaps, expected):
        assert abs(g - e) < 0.05, f"gaps {gaps} != {expected}"

    # a successful read (a timeout counts) resets the streak: fail, fail, ok+fail, fail
    reads = {0: [_eio()], 1: [_eio()], 2: [usb.core.USBTimeoutError("Operation timed out", -7, 10060), _eio()],
             3: [_eio()]}
    times, _, _ = _run_worker_clocked(lambda i: FakeDev(reads.get(i, [_eio()])), max_finds=5)
    gaps = [b - a for a, b in zip(times, times[1:])]
    # after find#2 the streak is 2 (1 s wait); the timeout on dev#2 resets it, so dev#2's
    # failure is the 1st of a new streak (0 s) and dev#3's the 2nd (1 s)
    expected = [0, 1, 0, 1]
    for g, e in zip(gaps, expected):
        assert abs(g - e) < 0.05, f"gaps {gaps} != {expected}"


# (8) stopping (running=False) during a backoff wait returns promptly
def test_worker_backoff_stop():
    # find#4 happens at ~3 s and is followed by a 4 s wait; stop at 4 s
    times, end, stopped = _run_worker_clocked(lambda i: FakeDev([_eio()]), max_finds=99, stop_at=4.0)
    assert stopped is not None, f"never stopped ({len(times)} finds)"
    assert end - stopped <= 0.5, f"worker kept sleeping {end - stopped:.2f} s after stop"


def _import_main():
    import logging as _logging
    class _NoFile(_logging.NullHandler):
        def __init__(self, *a, **k): super().__init__()
    with patched((_logging, "FileHandler", _NoFile)):   # don't create powermate.log on import
        import main
    return main


class SlowTimeoutDev(FakeDev):
    """A connected knob with no input: every read waits like a real 100 ms poll, then times out."""
    def read(self, ep, size, timeout=None):
        time.sleep(0.1)
        raise usb.core.USBTimeoutError("Operation timed out", -7, 138)


# (9) stop(): the worker is joined first, the device is closed after it has exited
def test_stop_closes_after_worker():
    main = _import_main()
    events = []
    with patched(*_usb_noops()):
        dev = _dev_with(SlowTimeoutDev())
        real_close = dev.close
        def close():
            events.append("close"); real_close()
        dev.close = close
        fake_self = types.SimpleNamespace(running=True, device=dev, app_window=None,
                                          led=types.SimpleNamespace(stop=lambda: events.append("led")),
                                          gesture=types.SimpleNamespace(on_button=lambda p: None,
                                                                        on_knob=lambda k: None, update=lambda: None))
        def run():
            main.PowerMateApp._worker(fake_self)
            events.append("worker_exit")
        fake_self.worker_thread = threading.Thread(target=run, daemon=True)
        fake_self.worker_thread.start()
        time.sleep(0.05)   # the worker is now inside a 100 ms read
        main.PowerMateApp.stop(fake_self)
    assert events == ["led", "worker_exit", "close"], events
    assert dev.dev is None

    # a worker that does not finish within the join timeout: warn, then close anyway
    warnings, hold = [], threading.Event()
    class _Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING: warnings.append(record.getMessage())
    h = _Catch(); main.logger.addHandler(h)
    old_level = main.logger.level; main.logger.setLevel(logging.WARNING)   # root is CRITICAL here
    try:
        with patched(*_usb_noops()):
            dev = _dev_with(FakeDev())
            stuck = threading.Thread(target=hold.wait, daemon=True); stuck.start()
            fake_self = types.SimpleNamespace(running=True, device=dev, led=None, worker_thread=stuck)
            t0 = time.monotonic()
            main.PowerMateApp.stop(fake_self)
            assert time.monotonic() - t0 < 3, "stop() must not wait past the join timeout"
            assert dev.dev is None, "device must still be closed"
            assert warnings, "a stuck worker must be logged as a warning"
    finally:
        hold.set(); main.logger.removeHandler(h); main.logger.setLevel(old_level)


# (10) button = bit 0 of byte0: 0x03 -> pressed, 0x02 -> released
def test_worker_button_bit0():
    main = _import_main()
    presses = []
    fake_self = types.SimpleNamespace(running=True, app_window=None,
                                      device=_dev_with(FakeDev([[0x03, 0, 0, 0, 0, 0], [0x02, 0, 0, 0, 0, 0]])))
    def on_button(p):
        presses.append(p)
        if len(presses) >= 2:
            fake_self.running = False
    fake_self.gesture = types.SimpleNamespace(on_button=on_button, on_knob=lambda k: None, update=lambda: None)
    with patched(*_usb_noops(), (main.time, "sleep", lambda s: None)):
        main.PowerMateApp._worker(fake_self)
    assert presses == [True, False], presses


# (11) find() failing resets the read-error streak: a replugged knob's first error waits 0 s
def test_worker_find_fail_resets_streak():
    # find#0 err (streak 1, 0 s), find#1 err (streak 2, 1 s), find#2 absent (5 s), find#3 err -> 0 s
    times, _, _ = _run_worker_clocked(lambda i: None if i == 2 else FakeDev([_eio()]), max_finds=5)
    gaps = [b - a for a, b in zip(times, times[1:])]
    expected = [0, 1, 5, 0]
    assert len(gaps) == len(expected), (times, gaps)
    for g, e in zip(gaps, expected):
        assert abs(g - e) < 0.05, f"gaps {gaps} != {expected}"


# (12) TrayIcon.stop() called on the tray thread itself (Exit menu -> _quit) does not hang or raise
def test_tray_stop_from_own_thread():
    import tray
    result = {}
    class FakeIcon:
        def __init__(self, *a, **kw):
            self.stopped = threading.Event()
        def run(self):
            t0 = time.monotonic()
            try:
                ti.stop()   # what the Exit menu callback ends up doing
            except Exception as e:
                result["error"] = e
            result["elapsed"] = time.monotonic() - t0
            self.stopped.wait(2)
        def stop(self):
            self.stopped.set()
    with patched((tray, "Icon", FakeIcon), (tray, "Menu", lambda *a, **k: None),
                 (tray, "MenuItem", lambda *a, **k: None)):
        ti = tray.TrayIcon("missing.ico")
        ti.start()
        ti.thread.join(5)
    assert not ti.thread.is_alive(), "tray thread did not finish"
    assert "error" not in result, result
    assert result["elapsed"] < 0.5, result
    assert ti.icon.stopped.is_set()


# (13) stop() while the worker waits for an absent knob returns within 1 s, without a warning
def test_stop_during_absent_wait():
    main = _import_main()
    warnings = []
    class _Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING: warnings.append(record.getMessage())
    h = _Catch(); main.logger.addHandler(h)
    old_level = main.logger.level; main.logger.setLevel(logging.WARNING)   # root is CRITICAL here
    finds = []
    try:
        with patched(*_usb_noops(), (usb.core, "find", lambda **kw: finds.append(1))):   # always absent
            fake_self = types.SimpleNamespace(running=True, device=PowerMateDevice(), app_window=None, led=None,
                                              gesture=types.SimpleNamespace(on_button=lambda p: None,
                                                                            on_knob=lambda k: None, update=lambda: None))
            fake_self.worker_thread = threading.Thread(target=main.PowerMateApp._worker, args=(fake_self,), daemon=True)
            fake_self.worker_thread.start()
            time.sleep(0.3)   # the worker is now in the 5 s "not found" wait
            assert finds, "worker never searched for the device"
            t0 = time.monotonic()
            main.PowerMateApp.stop(fake_self)
            elapsed = time.monotonic() - t0
            fake_self.worker_thread.join(6)   # let a slow (old) worker finish before patches are undone
    finally:
        main.logger.removeHandler(h); main.logger.setLevel(old_level)
    assert elapsed < 1, f"stop() took {elapsed:.2f} s"
    assert not warnings, warnings


logging.basicConfig(level=logging.CRITICAL)
check("(1) timeout -> silent, no callback", test_timeout_is_silent)
check("(2) ENODEV/EIO/EPIPE USBError -> propagates", test_enodev_propagates)
check("(3) 6-byte report -> callback(button, signed delta)", test_normal_report)
check("(4) claim_interface failure -> find False, dev None", test_claim_failure)
check("find success / absent", test_find_success_and_absent)
check("(5) close vs set_brightness race -> no exception", test_close_vs_brightness_race)
check("(6) worker: read error -> close -> re-find", test_worker_reconnect)
check("(7) worker: repeated read errors -> growing re-find wait", test_worker_backoff)
check("(8) worker: stop during backoff wait returns promptly", test_worker_backoff_stop)
check("(9) stop(): close the device after the worker has exited", test_stop_closes_after_worker)
check("(10) worker: button = bit 0 of byte0", test_worker_button_bit0)
check("(11) worker: find() failure resets the read-error streak", test_worker_find_fail_resets_streak)
check("(12) TrayIcon.stop() on the tray thread itself", test_tray_stop_from_own_thread)
check("(13) stop() during the no-device wait -> < 1 s, no warning", test_stop_during_absent_wait)

print()
if fails:
    print(f"FAILED {len(fails)}: {fails}"); sys.exit(1)
print("ALL DEVICE TESTS PASSED")
