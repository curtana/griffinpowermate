"""State machine for detecting multi-click, long-press, and press+rotate gestures."""
import time
from enum import Enum
from typing import Callable, Optional
from dataclasses import dataclass

class Trigger(Enum):
    """All possible trigger events."""
    ROTATE_RIGHT = "rotate_right"
    ROTATE_LEFT = "rotate_left"
    PRESS = "press"
    DOUBLE_PRESS = "double_press"
    TRIPLE_PRESS = "triple_press"
    LONG_PRESS = "long_press"
    PRESS_ROTATE_RIGHT = "press_rotate_right"
    PRESS_ROTATE_LEFT = "press_rotate_left"


@dataclass
class GestureConfig:
    """Configuration for gesture detection."""
    multi_click_ms: int = 250        # Window to detect double/triple click
    long_press_ms: int = 500         # Time to fire long_press
    rotate_sensitivity: float = 1.0  # Rotation accumulation threshold
    rotate_acceleration: float = 0.0 # Faster rotation gives more delta


# Rotation acceleration curve (speed = knob detents per second). Starting values.
ACCEL_IDLE_RESET_S = 0.3   # pause longer than this restarts in precision mode
ACCEL_MIN_DT_S = 0.008     # floor for dt so a 0/tiny interval can't explode speed
ACCEL_SMOOTHING = 0.6      # speed = SMOOTHING*speed + (1-SMOOTHING)*instant
ACCEL_V0 = 4.0             # at/below this speed the multiplier is 1
ACCEL_VMAX = 40.0          # at/above this speed the multiplier is gmax
ACCEL_GAIN = 9.0           # gmax = 1 + GAIN * rotate_acceleration


class GestureDetector:
    """Detects gestures from raw button/knob events."""

    def __init__(self, callback: Callable[[Trigger, int], None], config: GestureConfig = None):
        """
        callback(trigger: Trigger, amount: int)
          - amount is the knob delta for rotations, 0 for clicks
        """
        self.callback = callback
        self.config = config or GestureConfig()

        # Button state machine
        self.button_pressed = False
        self.button_press_count = 0
        self.button_press_time = 0.0
        self.button_release_time = 0.0  # multi-click window starts at release
        self.long_press_fired = False
        self.multi_click_active = False

        # Rotation state
        self.accumulated_rotation = 0.0
        self.pressed_and_rotating = False
        self.last_rotate_time: Optional[float] = None
        self.rotate_speed = 0.0

    def on_button(self, pressed: bool, now: Optional[float] = None):
        """Handle button down/up event. `now` (seconds) is injectable for tests."""
        if now is None:
            now = time.time()

        if pressed:
            if not self.button_pressed:
                # Button just pressed
                self.button_pressed = True
                self.button_press_time = now
                self.long_press_fired = False
                self.pressed_and_rotating = False
                self.accumulated_rotation = 0.0
                self.button_press_count += 1
                self.multi_click_active = True
        else:
            if self.button_pressed:
                # Button just released
                self.button_pressed = False
                self.button_release_time = now

                if self.pressed_and_rotating:
                    # We rotated while pressed: this was a press+rotate gesture,
                    # so cancel any pending click detection for this press.
                    self.pressed_and_rotating = False
                    self.accumulated_rotation = 0.0
                    self.multi_click_active = False
                    self.button_press_count = 0
                elif self.long_press_fired:
                    # Long press already handled on this press; don't let the
                    # release start/continue a multi-click sequence.
                    self.multi_click_active = False
                    self.button_press_count = 0
                # else: normal click, wait for multi-click window in update()

    def on_knob(self, delta: int, now: Optional[float] = None):
        """Handle knob rotation delta. `now` (seconds) is injectable for tests."""
        if not delta:
            return  # button-only report: don't disturb rotation timing
        if now is None:
            now = time.time()

        # Accumulate rotation (sensitivity x acceleration multiplier)
        self.accumulated_rotation += (delta * self.config.rotate_sensitivity
                                      * self._accel_multiplier(delta, now))

        threshold = 1.0
        if self.accumulated_rotation >= threshold:
            amount = int(self.accumulated_rotation)
            self.accumulated_rotation -= amount
            self._fire_rotation(amount)

        elif self.accumulated_rotation <= -threshold:
            amount = int(abs(self.accumulated_rotation))
            self.accumulated_rotation += amount
            self._fire_rotation(-amount)

    def _accel_multiplier(self, delta: int, now: float) -> float:
        """Track rotation speed and return the acceleration multiplier (>= 1)."""
        last = self.last_rotate_time
        self.last_rotate_time = now
        dt = None if last is None else now - last
        if dt is None or dt < 0 or dt > ACCEL_IDLE_RESET_S:
            # First turn, after a pause, or clock went backwards: precision mode.
            self.rotate_speed = 0.0
            return 1.0

        inst = abs(delta) / max(dt, ACCEL_MIN_DT_S)
        self.rotate_speed = ACCEL_SMOOTHING * self.rotate_speed + (1 - ACCEL_SMOOTHING) * inst

        a = min(self.config.rotate_acceleration, 1.0)
        if a <= 0:
            return 1.0
        gmax = 1 + ACCEL_GAIN * a
        t = (self.rotate_speed - ACCEL_V0) / (ACCEL_VMAX - ACCEL_V0)
        t = min(max(t, 0.0), 1.0)
        return 1 + (gmax - 1) * t * t

    def _fire_rotation(self, amount: int):
        """Fire a rotation event."""
        if self.button_pressed:
            # Button is held, this is press+rotate
            self.pressed_and_rotating = True
            if amount > 0:
                self.callback(Trigger.PRESS_ROTATE_RIGHT, amount)
            else:
                self.callback(Trigger.PRESS_ROTATE_LEFT, abs(amount))
        else:
            # No button, just rotation
            if amount > 0:
                self.callback(Trigger.ROTATE_RIGHT, amount)
            else:
                self.callback(Trigger.ROTATE_LEFT, abs(amount))

    def update(self, now: Optional[float] = None):
        """
        Call this periodically (~100ms) to detect long-press and multi-click timeout.
        Typically called from the main event loop. `now` (seconds) is injectable for tests.
        """
        if now is None:
            now = time.time()

        # Detect long press (but not if the user is rotating while holding)
        if self.button_pressed and not self.long_press_fired and not self.pressed_and_rotating:
            elapsed = (now - self.button_press_time) * 1000
            if elapsed >= self.config.long_press_ms:
                self.long_press_fired = True
                self.button_press_count = 0  # Reset so multi-click doesn't fire after long press
                self.callback(Trigger.LONG_PRESS, 0)

        # Detect multi-click timeout (gap between clicks counts from the last release)
        if self.multi_click_active and not self.button_pressed:
            elapsed = (now - self.button_release_time) * 1000
            if elapsed >= self.config.multi_click_ms:
                self.multi_click_active = False

                if self.button_press_count == 1:
                    if not self.long_press_fired:
                        self.callback(Trigger.PRESS, 0)
                elif self.button_press_count == 2:
                    self.callback(Trigger.DOUBLE_PRESS, 0)
                elif self.button_press_count >= 3:
                    self.callback(Trigger.TRIPLE_PRESS, 0)

                self.button_press_count = 0
