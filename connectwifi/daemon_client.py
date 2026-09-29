"""whisplay-daemon's app protocol, spoken directly.

The daemon owns the HAT's screen, button and LED. An app registers,
subscribes to events, takes the foreground and then draws into a shared
RGB565 framebuffer file that the daemon copies to the LCD. This is the
part of Whisplay's runtime/whisplay_client.py this app needs, written
against the protocol (version 1, Whisplay's APP_INTEGRATION.md) so that
nothing is loaded from a Whisplay checkout: removing or changing
Whisplay's own apps, examples or runtime cannot break this one. The only
thing it needs from Whisplay is the daemon, running.
"""

from __future__ import annotations

import json
import mmap
import socket
import threading
import time

SOCKET_PATH = "/tmp/whisplay-daemon.sock"
PROTOCOL_VERSION = 1


class DaemonError(RuntimeError):
    pass


def request(cmd: str, payload: dict | None = None, socket_path: str = SOCKET_PATH,
            timeout: float = 5.0) -> dict:
    """One command, one reply: the daemon answers each connection once."""
    body = {"version": PROTOCOL_VERSION, "cmd": cmd, "payload": payload or {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(socket_path)
        client.sendall((json.dumps(body) + "\n").encode("utf-8"))
        line = client.makefile("r", encoding="utf-8").readline().strip()
    if not line:
        raise DaemonError(f"no reply from whisplay-daemon to {cmd}")
    reply = json.loads(line)
    if not reply.get("ok"):
        raise DaemonError(reply.get("error") or f"whisplay-daemon refused {cmd}")
    return reply.get("payload") or {}


def daemon_running(socket_path: str = SOCKET_PATH) -> bool:
    try:
        request("health.ping", socket_path=socket_path, timeout=2.0)
        return True
    except (OSError, ValueError, DaemonError):
        return False


class DaemonBoard:
    """The HAT as the app sees it under whisplay-daemon: the same methods
    as Whisplay's WhisplayBoard that this app calls, nothing more."""

    LCD_WIDTH = 240
    LCD_HEIGHT = 280

    def __init__(self, registration: dict, socket_path: str = SOCKET_PATH):
        self.registration = dict(registration)
        self.app_id = self.registration["app_id"]
        self.socket_path = socket_path
        self._callbacks = {"press": None, "release": None, "exit": None, "revoked": None}
        self._running = False
        self._listener: threading.Thread | None = None
        self._subscribed = threading.Event()
        self._lock = threading.Lock()
        self._token: str | None = None
        self._fb_file = None
        self._fb: mmap.mmap | None = None
        self._stride = self.LCD_WIDTH * 2

    def _request(self, cmd: str, payload: dict | None = None) -> dict:
        return request(cmd, payload, socket_path=self.socket_path)

    # ---------- lifecycle ----------

    def start(self, timeout: float = 5.0):
        """Register, listen for events, then take the foreground. Listening
        comes first so no button event between the two is missed."""
        self._request("app.register", self.registration)
        self._running = True
        self._listener = threading.Thread(target=self._event_loop, name="daemon-events", daemon=True)
        self._listener.start()
        self._subscribed.wait(timeout)
        self._acquire_foreground(timeout)

    def _acquire_foreground(self, timeout: float):
        # Right after launch the daemon may still be switching over from its
        # desktop, so a first refusal is normal; keep asking for a while.
        deadline = time.monotonic() + timeout
        while True:
            try:
                token = self._request("app.focus.acquire", {"app_id": self.app_id})["session_token"]
                fb = self._request("framebuffer.acquire", {"app_id": self.app_id, "session_token": token})
                self._attach(token, fb)
                return
            except (OSError, ValueError, KeyError, DaemonError) as exc:
                if time.monotonic() >= deadline:
                    raise DaemonError(f"could not take the screen: {exc}") from exc
                time.sleep(0.2)

    def _attach(self, token: str, fb: dict):
        with self._lock:
            self._detach_locked()
            self._fb_file = open(fb["buffer_handle"], "r+b")
            self._fb = mmap.mmap(self._fb_file.fileno(), 0)
            self._token = token
            self._stride = int(fb.get("stride") or self.LCD_WIDTH * 2)
            self.LCD_WIDTH = int(fb.get("width") or self.LCD_WIDTH)
            self.LCD_HEIGHT = int(fb.get("height") or self.LCD_HEIGHT)

    def _detach_locked(self):
        for handle in (self._fb, self._fb_file):
            if handle is not None:
                try:
                    handle.close()
                except (OSError, ValueError):
                    pass
        self._fb = None
        self._fb_file = None

    def release_focus(self):
        with self._lock:
            token = self._token
            self._token = None
            self._detach_locked()
        if token:
            try:
                self._request("app.focus.release", {"app_id": self.app_id, "session_token": token})
            except (OSError, ValueError, DaemonError):
                pass  # the daemon may be gone, or have taken the screen back already

    def cleanup(self):
        self._running = False
        self.release_focus()

    # ---------- drawing ----------

    def draw_image(self, x: int, y: int, width: int, height: int, pixel_data: bytes):
        with self._lock:
            fb = self._fb
            if fb is None:
                return  # not foreground: the buffer is not ours to write
            row_bytes = width * 2
            if x == 0 and row_bytes == self._stride:
                start = y * self._stride
                fb[start:start + row_bytes * height] = pixel_data[:row_bytes * height]
                return
            for row in range(height):
                src = row * row_bytes
                dst = (y + row) * self._stride + x * 2
                fb[dst:dst + row_bytes] = pixel_data[src:src + row_bytes]

    # ---------- events ----------

    def on_button_press(self, callback):
        self._callbacks["press"] = callback

    def on_button_release(self, callback):
        self._callbacks["release"] = callback

    def on_exit_request(self, callback):
        self._callbacks["exit"] = callback

    def on_focus_revoked(self, callback):
        self._callbacks["revoked"] = callback

    def _event_loop(self):
        while self._running:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(self.socket_path)
                    body = {"version": PROTOCOL_VERSION, "cmd": "events.subscribe",
                            "payload": {"app_id": self.app_id}}
                    client.sendall((json.dumps(body) + "\n").encode("utf-8"))
                    reader = client.makefile("r", encoding="utf-8")
                    if not reader.readline().strip():
                        raise DaemonError("subscription not acknowledged")
                    self._subscribed.set()
                    for line in reader:
                        if not self._running:
                            return
                        if line.strip():
                            self.handle_event(json.loads(line))
            except (OSError, ValueError, DaemonError):
                pass
            if self._running:
                time.sleep(0.5)  # the daemon restarted; subscribe again

    def handle_event(self, event: dict):
        name = event.get("event")
        payload = event.get("payload") or {}
        if name == "app_focus_revoked":
            # The buffer is invalid from here on; stop writing to it at once.
            with self._lock:
                self._token = None
                self._detach_locked()
        callback = {
            "button_pressed": self._callbacks["press"],
            "button_released": self._callbacks["release"],
            "app_exit_requested": self._callbacks["exit"],
            "app_focus_revoked": self._callbacks["revoked"],
        }.get(name)
        if callback is None:
            return
        if name == "app_focus_revoked":
            callback(payload)
        else:
            callback()
