"""The Whisplay HAT: through whisplay-daemon, with this app's own client.

Under the daemon (the normal case) nothing is loaded from Whisplay: see
connectwifi/daemon_client.py. Only when no daemon is running does the app
fall back to driving the HAT itself, and that needs Whisplay's hardware
driver, runtime/whisplay.py, found through WHISPLAY_RUNTIME or the usual
places.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from connectwifi import APP_ID, DISPLAY_NAME, ICON
from connectwifi.daemon_client import SOCKET_PATH, DaemonBoard, daemon_running

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def registration(root: Path = PROJECT_ROOT) -> dict:
    """The desktop entry. The installer and every launch send the same one,
    so a first run by hand still leaves an entry that can start the app."""
    return {
        "app_id": os.getenv("WHISPLAY_APP_ID", APP_ID),
        "display_name": DISPLAY_NAME,
        "icon": ICON,
        "launch_command": str(root / "run.sh"),
        "cwd": str(root),
        "env": {"WHISPLAY_APP_ID": APP_ID},
        "exit_gesture": "quad_click",
        # 190 is the slot of Whisplay's built-in WiFi (Bluetooth 200, Volume
        # 180, Power 170), which the installer takes off the desktop.
        "priority": 190,
        "persist": True,
        "use_daemon_default_log": True,
        # Esc means "back" inside the app, not "leave it".
        "disable_esc_exit_key": True,
    }


def runtime_candidates() -> list[Path]:
    candidates = []
    env_path = os.getenv("WHISPLAY_RUNTIME")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates += [
        Path.home() / "Whisplay" / "runtime",
        Path.home() / "ai-chatbot" / "Whisplay" / "runtime",
        PROJECT_ROOT.parent / "Whisplay" / "runtime",
        Path("/home/pi/Whisplay/runtime"),
        Path("/opt/whisplay/runtime"),
        Path("/usr/local/share/whisplay/runtime"),
    ]
    return candidates


def find_driver() -> Path | None:
    """Whisplay's hardware driver, for running without the daemon."""
    for candidate in runtime_candidates():
        if (candidate / "whisplay.py").is_file():
            return candidate
    return None


def create_board(socket_path: str = SOCKET_PATH):
    if daemon_running(socket_path):
        board = DaemonBoard(registration(), socket_path=socket_path)
        board.start()
        print("[connectwifi] drawing through whisplay-daemon", flush=True)
        return board

    driver = find_driver()
    if driver is None:
        raise SystemExit(
            "whisplay-daemon is not running, and Whisplay's hardware driver (whisplay.py) "
            "was not found to drive the HAT without it. Start the daemon: "
            "sudo systemctl start whisplay-daemon"
        )
    if str(driver) not in sys.path:
        sys.path.insert(0, str(driver))
    from whisplay import WhisplayBoard

    print(f"[connectwifi] no whisplay-daemon; driving the HAT with {driver}/whisplay.py", flush=True)
    return WhisplayBoard()
