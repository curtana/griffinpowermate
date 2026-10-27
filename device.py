"""USB communication with Griffin PowerMate knob."""
import errno
import os
import sys
import usb.core
import usb.util
import usb.backend.libusb1
import threading
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_backend = None


def _find_libusb_dll() -> Optional[str]:
    """Locate a bundled libusb-1.0.dll so pyusb has a backend without the
    DLL needing to be on PATH (works both from source and in a frozen EXE)."""
    # 1) Inside a PyInstaller bundle (onefile extracts to sys._MEIPASS).
    search_dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        search_dirs.append(meipass)
    # 2) Next to the executable / script.
    search_dirs.append(os.path.dirname(os.path.abspath(sys.argv[0] or __file__)))
    for d in search_dirs:
        for name in ("libusb-1.0.dll", "libusb0.dll"):
            cand = os.path.join(d, name)
            if os.path.exists(cand):
                return cand
    # 2) The 'libusb' pip package bundles per-arch DLLs.
    try:
        import libusb
        arch = "x86_64" if sys.maxsize > 2 ** 32 else "x86"
        base = os.path.dirname(libusb.__file__)
        cand = os.path.join(base, "_platform", "windows", arch, "libusb-1.0.dll")
        if os.path.exists(cand):
            return cand
    except Exception as e:
        logger.debug(f"libusb package not available: {e}")
    return None


def get_backend():
    """Return a cached libusb1 backend, preferring the bundled DLL."""
    global _backend
    if _backend is not None:
        return _backend
    dll = _find_libusb_dll()
    if dll:
        logger.info(f"Using libusb DLL: {dll}")
        _backend = usb.backend.libusb1.get_backend(find_library=lambda x: dll)
    if _backend is None:
        # Fall back to whatever pyusb can discover on the system.
        _backend = usb.backend.libusb1.get_backend()
    return _backend

# USB identifiers
VENDOR_ID = 0x077d
PRODUCT_ID = 0x0410
ENDPOINT_IN = 0x81
ENDPOINT_OUT = 0x02

# LED control commands
CMD_SET_STATIC_BRIGHTNESS = 0x01
CMD_SET_PULSE_AWAKE = 0x03
CMD_SET_PULSE_MODE = 0x04

# USB timeouts (ms)
TIMEOUT_INTERRUPT_READ = 5000
TIMEOUT_LED_WRITE = 2000

LIBUSB_ERROR_TIMEOUT = -7
_ETIMEDOUT = {110, getattr(errno, "ETIMEDOUT", 110)}  # Linux 110; Windows Python 10060


def _is_timeout(e: "usb.core.USBError") -> bool:
    """True if a USBError is a read timeout (normal: no input during the poll)."""
    timeout_cls = getattr(usb.core, "USBTimeoutError", None)  # pyusb >= 1.2
    if timeout_cls is not None and isinstance(e, timeout_cls):
        return True
    if getattr(e, "backend_error_code", None) == LIBUSB_ERROR_TIMEOUT:
        return True
    if getattr(e, "errno", None) in _ETIMEDOUT:
        return True
    # Message varies by backend ("Operation timed out", "timeout").
    msg = str(e).lower()
    return "timeout" in msg or "timed out" in msg


class PowerMateDevice:
    """Handles USB communication with PowerMate device."""

    def __init__(self):
        self.dev: Optional[usb.core.Device] = None
        self.running = False
        self._lock = threading.Lock()

    def find(self) -> bool:
        """Find and open the PowerMate device. Returns True if found."""
        with self._lock:
            try:
                self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID,
                                         backend=get_backend())
                if self.dev is None:
                    return False
                # On Windows/WinUSB the OS has already configured the device, and
                # calling set_configuration() invalidates the endpoint pipe handles
                # (causing "Entity not found" on reads). Only configure elsewhere.
                if not sys.platform.startswith("win"):
                    try:
                        self.dev.set_configuration()
                    except usb.core.USBError as e:
                        logger.debug(f"set_configuration note: {e}")
                try:
                    usb.util.claim_interface(self.dev, 0)
                except usb.core.USBError as e:
                    # Without the interface we cannot read; report "not found" so the
                    # worker retries later (e.g. another process holds the device).
                    logger.warning(f"Cannot claim PowerMate interface: {e}")
                    try:
                        usb.util.dispose_resources(self.dev)
                    except Exception as e2:
                        logger.debug(f"dispose_resources note: {e2}")
                    self.dev = None
                    return False
                logger.info("PowerMate device found and opened")
                return True
            except usb.core.USBError as e:
                logger.debug(f"USB error finding device: {e}")
                self.dev = None
                return False

    def close(self):
        """Close the USB device."""
        with self._lock:
            try:
                if self.dev:
                    usb.util.release_interface(self.dev, 0)
                    usb.util.dispose_resources(self.dev)
            except Exception as e:
                logger.debug(f"Error closing device: {e}")
            finally:
                self.dev = None

    def read_events(self, callback: Callable[[int, int], None], timeout_ms: int = 500):
        """
        Read interrupt events from the device.
        callback(button: int, knob_delta: int) - button: 1=pressed, 0=released; knob_delta: signed

        A timeout (no input during the poll window) returns silently. Any other
        USBError (device unplugged, pipe error, ...) is re-raised so the worker
        closes the device and searches for it again.
        """
        dev = self.dev
        if not dev:
            return

        try:
            data = dev.read(ENDPOINT_IN, 6, timeout=timeout_ms)
        except usb.core.USBError as e:
            if _is_timeout(e):
                return
            logger.warning(f"USB read error: {e}")
            raise

        try:
            if len(data) >= 2:
                button = int(data[0])
                knob_delta = int(data[1])
                # Convert unsigned to signed for knob_delta
                if knob_delta > 127:
                    knob_delta = knob_delta - 256
                callback(button, knob_delta)
        except Exception as e:
            logger.debug(f"Error handling events: {e}")

    def set_brightness(self, brightness: float):
        """Set LED brightness 0.0-1.0.

        Uses an interrupt-OUT write to endpoint 0x02 (the original PowerMate
        method). This is critical on Windows/WinUSB: a vendor *control* transfer
        for brightness permanently breaks the interrupt-IN read pipe
        ("Entity not found"), whereas the interrupt-OUT write does not disturb it.
        """
        brightness_byte = int(max(0, min(255, brightness * 255)))

        # Check self.dev under the lock: the worker may close() the device at any
        # time, and a failed LED write must never kill the LED thread.
        with self._lock:
            dev = self.dev
            if not dev:
                return
            try:
                dev.write(ENDPOINT_OUT, bytes([brightness_byte]), timeout=TIMEOUT_LED_WRITE)
                return
            except Exception as e:
                logger.debug(f"Interrupt LED write failed: {e}")

            # Fallback: vendor control transfer. Only on non-Windows, because on
            # WinUSB it breaks subsequent interrupt reads.
            if not sys.platform.startswith("win"):
                try:
                    dev.ctrl_transfer(0x41, CMD_SET_STATIC_BRIGHTNESS,
                                      0x0001, brightness_byte, 0, TIMEOUT_LED_WRITE)
                except Exception as e:
                    logger.debug(f"Control LED write failed: {e}")

    def set_pulse_awake(self, enabled: bool):
        """Enable/disable hardware pulse when device is awake.

        Uses a vendor control transfer, which breaks the interrupt-IN read pipe
        on Windows/WinUSB, so it is disabled there (LED pulse is done in software
        via set_brightness instead — see led.py).
        """
        if sys.platform.startswith("win"):
            return

        with self._lock:
            dev = self.dev
            if not dev:
                return
            try:
                dev.ctrl_transfer(
                    bmRequestType=0x41,
                    bRequest=CMD_SET_PULSE_AWAKE,
                    wValue=0x0003,
                    wIndex=1 if enabled else 0,
                    data_or_wLength=0,
                    timeout=TIMEOUT_LED_WRITE
                )
            except Exception as e:
                logger.debug(f"Set pulse awake failed: {e}")

    def set_pulse_mode(self, pulse_table: int, pulse_speed: int):
        """
        Set hardware pulse mode.
        pulse_speed: 0-510
          0-254: divide (slow)
          255: normal
          256-510: multiply (fast)

        Disabled on Windows/WinUSB (control transfers break interrupt reads).
        """
        if sys.platform.startswith("win"):
            return

        # Encode operation and argument
        if pulse_speed < 255:
            op = 0  # divide
            arg = 255 - pulse_speed
        elif pulse_speed == 255:
            op = 1  # normal
            arg = 0
        else:
            op = 2  # multiply
            arg = pulse_speed - 255

        with self._lock:
            dev = self.dev
            if not dev:
                return
            try:
                dev.ctrl_transfer(
                    bmRequestType=0x41,
                    bRequest=CMD_SET_PULSE_MODE,
                    wValue=(pulse_table << 8) | 0x04,
                    wIndex=(arg << 8) | op,
                    data_or_wLength=0,
                    timeout=TIMEOUT_LED_WRITE
                )
            except Exception as e:
                logger.debug(f"Set pulse mode failed: {e}")
