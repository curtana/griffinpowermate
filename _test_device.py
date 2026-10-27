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
        sleeps.append(s)

    with patched(*_usb_noops(), (usb.core, "find", fake_find), (main.time, "sleep", fake_sleep)):
        main.PowerMateApp._worker(fake_self)

    assert finds == [first, None, second], finds
    assert fake_self.device.dev is second
    assert statuses == ["PowerMate connected", "Device error", "PowerMate not found", "PowerMate connected"], statuses
    # exactly one 5-second wait while the device was absent; no 5 s wait between close and re-find
    assert sleeps.count(5) == 1, sleeps


logging.basicConfig(level=logging.CRITICAL)
check("(1) timeout -> silent, no callback", test_timeout_is_silent)
check("(2) ENODEV/EIO/EPIPE USBError -> propagates", test_enodev_propagates)
check("(3) 6-byte report -> callback(button, signed delta)", test_normal_report)
check("(4) claim_interface failure -> find False, dev None", test_claim_failure)
check("find success / absent", test_find_success_and_absent)
check("(5) close vs set_brightness race -> no exception", test_close_vs_brightness_race)
check("(6) worker: read error -> close -> re-find", test_worker_reconnect)

print()
if fails:
    print(f"FAILED {len(fails)}: {fails}"); sys.exit(1)
print("ALL DEVICE TESTS PASSED")
