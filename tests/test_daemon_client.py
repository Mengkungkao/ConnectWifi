"""The app's own whisplay-daemon client, against a fake daemon on a real
Unix socket, and proof that the app loads nothing from Whisplay."""

import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from conftest import ROOT

from connectwifi import board as boardmod
from connectwifi.board import registration
from connectwifi.daemon_client import DaemonBoard, DaemonError, daemon_running

FRAME_BYTES = 240 * 280 * 2


class FakeDaemon:
    """Answers the protocol the way whisplay-daemon does: one reply per
    connection, except events.subscribe, which stays open for events."""

    def __init__(self, folder):
        self.path = str(folder / "daemon.sock")
        self.fb_path = str(folder / "fb.bin")
        self.requests = []
        self.subscribers = []
        self.refuse_focus = 0
        self.subscribed = threading.Event()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        line = conn.makefile("r").readline()
        if not line:
            conn.close()
            return
        req = json.loads(line)
        self.requests.append(req)
        cmd, payload = req["cmd"], req.get("payload", {})
        if cmd == "events.subscribe":
            conn.sendall(b'{"ok": true, "payload": {"subscribed": true}}\n')
            self.subscribers.append(conn)
            self.subscribed.set()
            return
        if cmd == "app.focus.acquire" and self.refuse_focus:
            self.refuse_focus -= 1
            reply = {"ok": False, "error": "another app is pending foreground"}
        elif cmd == "app.focus.acquire":
            reply = {"ok": True, "payload": {"app_id": payload["app_id"], "session_token": "tok1"}}
        elif cmd == "framebuffer.acquire":
            with open(self.fb_path, "wb") as fp:
                fp.write(bytes(FRAME_BYTES))
            reply = {"ok": True, "payload": {"width": 240, "height": 280, "stride": 480,
                                             "pixel_format": "RGB565", "buffer_handle": self.fb_path,
                                             "session_token": "tok1"}}
        else:
            reply = {"ok": True, "payload": {}}
        conn.sendall((json.dumps(reply) + "\n").encode())
        conn.close()

    def send_event(self, name, payload=None):
        message = {"event": name}
        if payload:
            message["payload"] = payload
        for conn in self.subscribers:
            conn.sendall((json.dumps(message) + "\n").encode())

    def commands(self):
        return [req["cmd"] for req in self.requests]

    def framebuffer(self):
        with open(self.fb_path, "rb") as fp:
            return fp.read()

    def close(self):
        self.server.close()
        for conn in self.subscribers:
            conn.close()


@pytest.fixture
def daemon(tmp_path):
    fake = FakeDaemon(tmp_path)
    yield fake
    fake.close()


@pytest.fixture
def board(daemon):
    started = DaemonBoard(registration(), socket_path=daemon.path)
    started.start()
    yield started
    started.cleanup()


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_daemon_running(daemon, tmp_path):
    assert daemon_running(daemon.path)
    assert not daemon_running(str(tmp_path / "nothing.sock"))


def test_start_registers_listens_then_takes_the_screen(daemon, board):
    assert daemon.commands()[:4] == ["app.register", "events.subscribe", "app.focus.acquire",
                                     "framebuffer.acquire"]
    entry = daemon.requests[0]["payload"]
    assert entry["app_id"] == "connectwifi" and entry["launch_command"].endswith("/run.sh")
    assert entry["disable_esc_exit_key"] is True and entry["exit_gesture"] == "quad_click"
    assert daemon.requests[0]["version"] == 1


def test_a_first_refusal_of_the_screen_is_retried(daemon):
    daemon.refuse_focus = 2
    started = DaemonBoard(registration(), socket_path=daemon.path)
    started.start()
    assert daemon.commands().count("app.focus.acquire") == 3
    started.cleanup()


def test_giving_up_on_the_screen_says_why(daemon):
    daemon.refuse_focus = 1000
    with pytest.raises(DaemonError, match="pending foreground"):
        DaemonBoard(registration(), socket_path=daemon.path).start(timeout=0.5)


def test_draw_image_writes_into_the_shared_framebuffer(daemon, board):
    frame = bytes(range(256)) * (FRAME_BYTES // 256) + bytes(FRAME_BYTES % 256)
    board.draw_image(0, 0, 240, 280, frame)
    assert daemon.framebuffer() == frame
    board.draw_image(10, 5, 2, 2, b"\xAA\xBB\xCC\xDD\x11\x22\x33\x44")
    buffer = daemon.framebuffer()
    assert buffer[5 * 480 + 20:5 * 480 + 24] == b"\xAA\xBB\xCC\xDD"
    assert buffer[6 * 480 + 20:6 * 480 + 24] == b"\x11\x22\x33\x44"


def test_events_reach_the_callbacks(daemon, board):
    seen = []
    board.on_button_press(lambda: seen.append("press"))
    board.on_button_release(lambda: seen.append("release"))
    board.on_exit_request(lambda: seen.append("exit"))
    assert daemon.subscribed.wait(2)
    daemon.send_event("button_pressed", {"app_id": "connectwifi"})
    daemon.send_event("button_released", {"app_id": "connectwifi"})
    daemon.send_event("app_foreground_acquired", {"app_id": "connectwifi"})
    daemon.send_event("app_exit_requested", {"app_id": "connectwifi", "reason": "gesture"})
    assert wait_for(lambda: len(seen) == 3)
    assert seen == ["press", "release", "exit"]


def test_losing_the_screen_stops_drawing_at_once(daemon, board):
    revoked = []
    board.on_focus_revoked(revoked.append)
    assert daemon.subscribed.wait(2)
    daemon.send_event("app_focus_revoked", {"app_id": "connectwifi", "reason": "exit"})
    assert wait_for(lambda: revoked)
    assert revoked == [{"app_id": "connectwifi", "reason": "exit"}]
    board.draw_image(0, 0, 240, 280, b"\xFF" * FRAME_BYTES)
    assert daemon.framebuffer() == bytes(FRAME_BYTES)
    board.cleanup()
    assert "app.focus.release" not in daemon.commands()      # nothing left to release


def test_cleanup_hands_the_screen_back(daemon, board):
    board.cleanup()
    release = [req for req in daemon.requests if req["cmd"] == "app.focus.release"]
    assert release and release[0]["payload"] == {"app_id": "connectwifi", "session_token": "tok1"}


def test_without_daemon_or_driver_the_error_says_what_to_start(tmp_path, monkeypatch):
    monkeypatch.setattr(boardmod, "runtime_candidates", lambda: [tmp_path])
    with pytest.raises(SystemExit, match="whisplay-daemon is not running"):
        boardmod.create_board(socket_path=str(tmp_path / "nothing.sock"))


def test_the_app_loads_nothing_from_whisplay(daemon, tmp_path):
    """Run the app's start-up in a fresh interpreter with no Whisplay on
    the machine as far as it can tell: a bare HOME, no WHISPLAY_RUNTIME.
    It must reach the screen and draw, with no Whisplay module loaded."""
    home = tmp_path / "home"
    home.mkdir()
    script = f"""
import sys
from connectwifi.app import ConnectWifiApp
from connectwifi.board import create_board
board = create_board(socket_path={daemon.path!r})
app = ConnectWifiApp(board)
app.render()
board.cleanup()
loaded = sorted(name for name, module in sys.modules.items()
                if "whisplay" in name.lower()
                or "whisplay" in (getattr(module, "__file__", "") or "").lower().replace("connectwifi", ""))
print("WHISPLAY MODULES:", loaded)
"""
    env = {key: value for key, value in os.environ.items() if key != "WHISPLAY_RUNTIME"}
    env.update(HOME=str(home), PYTHONPATH=ROOT)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            env=env, cwd=str(tmp_path), timeout=30)
    assert result.returncode == 0, result.stderr
    assert "WHISPLAY MODULES: []" in result.stdout
    assert daemon.commands()[:5] == ["health.ping", "app.register", "events.subscribe",
                                     "app.focus.acquire", "framebuffer.acquire"]
    assert daemon.framebuffer() != bytes(FRAME_BYTES)          # it drew a frame
