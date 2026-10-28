"""Action editor widget for configuring trigger actions."""
import logging
import customtkinter as ctk
from typing import Dict, Any, List, Optional
import tkinter as tk

from config import DEFAULT_ACTION_AMOUNTS, to_number

logger = logging.getLogger(__name__)


class ActionEditor(ctk.CTkFrame):
    """Widget to edit a single action configuration."""

    ACTIONS = [
        "none",
        "volume_up",
        "volume_down",
        "mute",
        "mic_mute_toggle",
        "media_play_pause",
        "media_next",
        "media_prev",
        "scroll_up",
        "scroll_down",
        "key",
        "macro",
        "launch",
        "run"
    ]

    def __init__(self, parent, trigger_name: str):
        super().__init__(parent)
        self.trigger_name = trigger_name
        self.current_action: Optional[str] = None

        # UI elements
        self.action_dropdown: Optional[ctk.CTkComboBox] = None
        self.param_frame: Optional[ctk.CTkFrame] = None
        self.param_widgets: Dict[str, Any] = {}
        self.invalid_params: List[str] = []   # set by get_action()

    def create(self):
        """Create the action editor widget."""
        # Action dropdown
        self.action_dropdown = ctk.CTkComboBox(
            self,
            values=self.ACTIONS,
            command=self._on_action_changed,
            width=150
        )
        self.action_dropdown.pack(side="left", padx=5)

        # Parameter frame (changes based on action)
        self.param_frame = ctk.CTkFrame(self)
        self.param_frame.pack(side="left", fill="x", expand=True, padx=5)

    def _on_action_changed(self, action: str):
        """Handle action selection change."""
        self.current_action = action
        self._refresh_params(action)

    def _refresh_params(self, action: str):
        """Refresh parameter widgets based on action type."""
        # Clear old widgets
        for widget in self.param_frame.winfo_children():
            widget.destroy()
        self.param_widgets.clear()

        if action == "none":
            pass

        elif action in ["volume_up", "volume_down", "scroll_up", "scroll_down"]:
            ctk.CTkLabel(self.param_frame, text="Amount:").pack(side="left", padx=5)
            entry = ctk.CTkEntry(self.param_frame, width=80)
            entry.pack(side="left", padx=2)
            entry.insert(0, "0.04" if action.startswith("volume") else "3")
            self.param_widgets["amount"] = entry

        elif action == "key":
            ctk.CTkLabel(self.param_frame, text="Keys:").pack(side="left", padx=5)
            entry = ctk.CTkEntry(self.param_frame, width=150)
            entry.pack(side="left", padx=2)
            self.param_widgets["keys"] = entry

            # Record button
            ctk.CTkButton(
                self.param_frame,
                text="Record",
                command=lambda e=entry: self._record_into(e),
                width=60
            ).pack(side="left", padx=2)

        elif action == "macro":
            # Up to 3 key combos sent in sequence.
            ctk.CTkLabel(self.param_frame, text="Macro (3 steps):").pack(side="left", padx=5)
            for i in (1, 2, 3):
                entry = ctk.CTkEntry(self.param_frame, width=110, placeholder_text=f"step {i}")
                entry.pack(side="left", padx=2)
                ctk.CTkButton(self.param_frame, text="●", width=24,
                              command=lambda e=entry: self._record_into(e)).pack(side="left", padx=(0, 4))
                self.param_widgets[f"macro{i}"] = entry

        elif action in ["launch"]:
            ctk.CTkLabel(self.param_frame, text="Path/URL:").pack(side="left", padx=5)
            entry = ctk.CTkEntry(self.param_frame, width=200)
            entry.pack(side="left", padx=2)
            self.param_widgets["path"] = entry

        elif action == "run":
            ctk.CTkLabel(self.param_frame, text="Command:").pack(side="left", padx=5)
            entry = ctk.CTkEntry(self.param_frame, width=200)
            entry.pack(side="left", padx=2)
            self.param_widgets["command"] = entry

    # Tkinter keysym -> 'keyboard' library name normalisation
    _KEYSYM_MAP = {
        "Return": "enter", "Escape": "esc", "Prior": "page up",
        "Next": "page down", "BackSpace": "backspace", "Delete": "delete",
        "Up": "up", "Down": "down", "Left": "left", "Right": "right",
        "space": "space", "Tab": "tab", "Home": "home", "End": "end",
        # Punctuation keysyms -> the character name 'keyboard' knows.
        "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]",
        "backslash": "\\", "semicolon": ";", "apostrophe": "'", "comma": ",",
        "period": ".", "slash": "/", "grave": "`",
        # Shifted symbols (US layout) -> the unshifted base key, so that
        # Shift+1 records as "shift+1" rather than "shift+exclam".
        "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4",
        "percent": "5", "asciicircum": "6", "ampersand": "7", "asterisk": "8",
        "parenleft": "9", "parenright": "0", "underscore": "-", "plus": "=",
        "braceleft": "[", "braceright": "]", "bar": "\\", "colon": ";",
        "quotedbl": "'", "less": ",", "greater": ".", "question": "/",
        "asciitilde": "`",
    }

    # Tk event.state modifier bits. On Windows Tk reports Alt as 0x20000 and
    # uses 0x0008 for NumLock, so 0x0008 only means Alt elsewhere.
    _STATE_SHIFT = 0x0001
    _STATE_CONTROL = 0x0004
    _STATE_MOD1 = 0x0008
    _STATE_ALT_WIN = 0x20000

    @staticmethod
    def combo_from_key(keysym: str, state: int, keycode: Optional[int] = None,
                       platform: Optional[str] = None) -> str:
        """Turn a Tk key event into a 'keyboard'-style combo, e.g. 'ctrl+shift+1'.

        Only the modifiers actually held are included (CapsLock / NumLock are
        ignored) and the key name is the base key, so the result can be
        replayed with keyboard.send().
        """
        if platform is None:
            import sys
            platform = sys.platform
        win = platform == "win32"

        parts = []
        if state & ActionEditor._STATE_CONTROL:
            parts.append("ctrl")
        if state & ActionEditor._STATE_SHIFT:
            parts.append("shift")
        alt_mask = ActionEditor._STATE_ALT_WIN
        if not win:
            alt_mask |= ActionEditor._STATE_MOD1
        if state & alt_mask:
            parts.append("alt")

        # On Windows event.keycode is the virtual-key code; for letters and
        # top-row digits it equals the ASCII code of the key on every layout,
        # so it gives the base key even when the keysym is shifted or comes
        # from an IME.
        if win and keycode is not None and (0x30 <= keycode <= 0x39
                                            or 0x41 <= keycode <= 0x5A):
            key = chr(keycode).lower()
        else:
            key = ActionEditor._KEYSYM_MAP.get(keysym, keysym.lower())
        parts.append(key)
        return "+".join(parts)

    def _record_into(self, target_entry):
        """Open a modal that captures the next key combination into target_entry."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Record Shortcut")
        dialog.geometry("320x120")
        dialog.transient(self.winfo_toplevel())
        ctk.CTkLabel(
            dialog,
            text="Press the key combination...\n(Esc to cancel)",
            font=("Arial", 12),
        ).pack(expand=True, fill="both", padx=10, pady=10)
        dialog.after(100, dialog.grab_set)

        def on_key(event):
            keysym = event.keysym
            # Ignore lone modifier presses; wait for a real key.
            if keysym in ("Control_L", "Control_R", "Shift_L", "Shift_R",
                          "Alt_L", "Alt_R", "Win_L", "Win_R"):
                return
            if keysym == "Escape":
                dialog.destroy()
                return

            combo = self.combo_from_key(keysym, event.state, event.keycode)

            target_entry.delete(0, tk.END)
            target_entry.insert(0, combo)
            dialog.destroy()

        dialog.bind("<KeyPress>", on_key)
        dialog.focus_force()

    def load(self, action_config: Dict[str, Any]):
        """Load an action configuration."""
        if not action_config or not action_config.get("action"):
            action = "none"
        else:
            action = action_config["action"]

        if self.action_dropdown:
            # set() does not fire the combobox command, so update state manually.
            self.current_action = action
            self.action_dropdown.set(action)
            self._refresh_params(action)

            if action == "macro":
                # Populate the 3 step fields from the saved sequence list.
                seq = action_config.get("sequence", []) or []
                for i in (1, 2, 3):
                    w = self.param_widgets.get(f"macro{i}")
                    if w is not None:
                        w.delete(0, tk.END)
                        if i - 1 < len(seq):
                            w.insert(0, str(seq[i - 1]))
            else:
                # Load simple parameters by name.
                for param_name, widget in self.param_widgets.items():
                    if param_name in action_config:
                        value = action_config[param_name]
                        if isinstance(widget, ctk.CTkEntry):
                            widget.delete(0, tk.END)
                            widget.insert(0, str(value))

    def get_action(self) -> Dict[str, Any]:
        """Get current action configuration.

        Only `amount` is converted to a number; keys / path / command / macro
        steps stay text (so a shortcut "1" is the key 1, not scan code 1).
        An amount that isn't a number becomes the action's default amount and
        its name is left in self.invalid_params for the Save status line."""
        self.invalid_params = []
        if not self.current_action or self.current_action == "none":
            return {"action": "none"}

        config = {"action": self.current_action}

        if self.current_action == "macro":
            seq = []
            for i in (1, 2, 3):
                w = self.param_widgets.get(f"macro{i}")
                if w is not None:
                    v = w.get().strip()
                    if v:
                        seq.append(v)
            config["sequence"] = seq
            return config

        for param_name, widget in self.param_widgets.items():
            if isinstance(widget, ctk.CTkEntry):
                value = widget.get()
                if param_name == "amount":
                    number = to_number(value)
                    if number is None:
                        number = DEFAULT_ACTION_AMOUNTS.get(self.current_action, 1)
                        logger.warning(f"{self.trigger_name}: amount {value!r} is not a "
                                       f"number, using {number}")
                        self.invalid_params.append(param_name)
                    config[param_name] = number
                else:
                    config[param_name] = value

        return config
