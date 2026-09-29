"""USB or Bluetooth keyboard input, read straight from evdev.

The reader loop and key map come from Whisplay's daemon
(daemon/internal_apps/keyboard.py, PiSugar, Apache-2.0). They are copied
here rather than loaded from a Whisplay checkout so this app does not
depend on where, or at which version, Whisplay is installed.

The device filter is this app's own: only devices with a full letter row
count, which keeps HDMI-CEC remotes and the like out.
"""

from __future__ import annotations

import os
import select
import struct
import threading
import time

EV_KEY = 0x01
KEY_PRESS = 1
KEY_REPEAT = 2
KEY_RELEASE = 0
SHIFT_CODES = {42, 54}
KEY_ENTER = 28
KEY_KPENTER = 96
KEY_ESC = 1
KEY_BACKSPACE = 14
KEY_SPACE = 57
KEY_UP = 103
KEY_DOWN = 108
INPUT_EVENT_FORMAT = "llHHI"

KEYCODE_TO_CHAR = {
    2: ("1", "!"), 3: ("2", "@"), 4: ("3", "#"), 5: ("4", "$"), 6: ("5", "%"),
    7: ("6", "^"), 8: ("7", "&"), 9: ("8", "*"), 10: ("9", "("), 11: ("0", ")"),
    12: ("-", "_"), 13: ("=", "+"),
    16: ("q", "Q"), 17: ("w", "W"), 18: ("e", "E"), 19: ("r", "R"), 20: ("t", "T"),
    21: ("y", "Y"), 22: ("u", "U"), 23: ("i", "I"), 24: ("o", "O"), 25: ("p", "P"),
    26: ("[", "{"), 27: ("]", "}"),
    30: ("a", "A"), 31: ("s", "S"), 32: ("d", "D"), 33: ("f", "F"), 34: ("g", "G"),
    35: ("h", "H"), 36: ("j", "J"), 37: ("k", "K"), 38: ("l", "L"),
    39: (";", ":"), 40: ("'", '"'), 41: ("`", "~"), 43: ("\\", "|"),
    44: ("z", "Z"), 45: ("x", "X"), 46: ("c", "C"), 47: ("v", "V"), 48: ("b", "B"),
    49: ("n", "N"), 50: ("m", "M"), 51: (",", "<"), 52: (".", ">"), 53: ("/", "?"),
}

LETTER_KEYCODES = tuple(range(16, 26)) + tuple(range(30, 39)) + tuple(range(44, 51))


def has_letter_keys(key_bits: str) -> bool:
    """The kernel prints capability bitmaps as unsigned longs, most
    significant word first, so word width follows the userspace long."""
    words = key_bits.split()
    if not words:
        return False
    word_bits = struct.calcsize("l") * 8
    bitmap = 0
    for index, word in enumerate(reversed(words)):
        try:
            bitmap |= int(word, 16) << (word_bits * index)
        except ValueError:
            return False
    return all((bitmap >> code) & 1 for code in LETTER_KEYCODES)


def keyboard_event_names(devices_text: str) -> list[str]:
    """eventN names of real keyboards in /proc/bus/input/devices. HDMI-CEC
    receivers also claim a kbd handler but only carry remote keys, and
    udev gives them no *-kbd link, so filter on capabilities instead."""
    names: list[str] = []
    for block in devices_text.split("\n\n"):
        handlers: list[str] = []
        key_bits = ""
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1].split()
            elif line.startswith("B: KEY="):
                key_bits = line.split("=", 1)[1]
        if "kbd" not in handlers or not has_letter_keys(key_bits):
            continue
        names += [token for token in handlers if token.startswith("event") and token not in names]
    return names


def keyboard_device_paths() -> list[str]:
    try:
        with open("/proc/bus/input/devices", "r", encoding="utf-8") as fp:
            text = fp.read()
    except OSError:
        return []
    paths = [os.path.join("/dev/input", name) for name in keyboard_event_names(text)]
    return [path for path in paths if os.access(path, os.R_OK)]


def key_action(code: int, shift: bool):
    """What a key press means to the app: a named action, a character, or None."""
    named = {
        KEY_UP: "up", KEY_DOWN: "down", KEY_ENTER: "submit", KEY_KPENTER: "submit",
        KEY_ESC: "cancel", KEY_BACKSPACE: "backspace",
    }
    if code in named:
        return named[code]
    if code == KEY_SPACE:
        return ("char", " ")
    chars = KEYCODE_TO_CHAR.get(code)
    if not chars:
        return None
    return ("char", chars[1] if shift else chars[0])


class KeyboardReader:
    """Calls callback(action) for each key press, on its own thread, and
    picks up keyboards plugged in (or paired) while it runs."""

    RESCAN_SEC = 2.0

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._callback = None
        self._shift_down = False

    def start(self, callback):
        self.stop()
        self._callback = callback
        self._stop_event = threading.Event()
        self._shift_down = False
        self._thread = threading.Thread(target=self._loop, args=(self._stop_event,),
                                        name="keyboard", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._thread = None
        self._callback = None
        self._shift_down = False

    def _loop(self, stop_event: threading.Event):
        event_size = struct.calcsize(INPUT_EVENT_FORMAT)
        open_fds: dict[int, str] = {}
        last_scan = 0.0

        def close(fd):
            open_fds.pop(fd, None)
            try:
                os.close(fd)
            except OSError:
                pass

        try:
            while not stop_event.is_set():
                now = time.monotonic()
                if now - last_scan >= self.RESCAN_SEC:
                    last_scan = now
                    current = set(keyboard_device_paths())
                    for fd, path in list(open_fds.items()):
                        if path not in current:
                            close(fd)
                    for path in current - set(open_fds.values()):
                        try:
                            open_fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
                        except OSError:
                            continue

                if not open_fds:
                    stop_event.wait(1.0)
                    continue

                try:
                    ready, _, _ = select.select(list(open_fds), [], [], 0.05)
                except (ValueError, OSError):
                    for fd in list(open_fds):
                        close(fd)
                    continue

                for fd in ready:
                    try:
                        data = os.read(fd, event_size * 32)
                    except OSError:
                        close(fd)
                        continue
                    for offset in range(0, len(data) - event_size + 1, event_size):
                        _, _, event_type, code, value = struct.unpack(
                            INPUT_EVENT_FORMAT, data[offset:offset + event_size]
                        )
                        self.handle_event(event_type, code, value)
        finally:
            for fd in list(open_fds):
                close(fd)

    def handle_event(self, event_type: int, code: int, value: int):
        if event_type != EV_KEY:
            return
        if code in SHIFT_CODES:
            if value == KEY_PRESS:
                self._shift_down = True
            elif value == KEY_RELEASE:
                self._shift_down = False
            return
        callback = self._callback
        if value not in (KEY_PRESS, KEY_REPEAT) or callback is None:
            return
        action = key_action(code, self._shift_down)
        if action is not None:
            callback(action)
