"""The app: a menu, a Wi-Fi scan list, SSID and password entry from a USB
or Bluetooth keyboard, and a switch for the BLE setup service.

The HAT's one button steps (short press) and selects (hold for a second).
Typing needs a keyboard; without one, the BLE service is the way on.
"""

from __future__ import annotations

import threading
import time

from connectwifi import network
from connectwifi.ble import BleService, BleStatus
from connectwifi.keyboard import KeyboardReader, keyboard_device_paths
from connectwifi.screens import (
    MENU_COUNT, MENU_EXIT, MENU_SCAN, MENU_TOGGLE_BLE, MODE_CONNECTING, MODE_MENU,
    MODE_PASSWORD, MODE_RESULT, MODE_SCAN, MODE_SSID, SCAN_LEADING, VISIBLE_SCAN_ROWS,
    Screens, View, rgb565_bytes,
)
from connectwifi.system import Commands, output_of

POLL_INTERVAL_SEC = 2.0
FRAME_INTERVAL_SEC = 0.08
LONG_PRESS_SEC = 1.0
SSID_MAX_LEN = 32
PASSWORD_MAX_LEN = 63
WPA_MIN_LEN = 8
# First pass shows only what is comfortably in range; Rescan drops the floor.
NEARBY_MIN_SIGNAL = 40


def _start_daemon_thread(target):
    threading.Thread(target=target, daemon=True).start()


class ConnectWifiApp:
    def __init__(self, board, commands: Commands | None = None, ble: BleService | None = None,
                 keyboard=None, start_thread=_start_daemon_thread, keyboard_paths=keyboard_device_paths):
        self.board = board
        self.commands = commands or Commands()
        self.ble = ble or BleService(self.commands)
        self.keyboard = keyboard
        self._start_thread = start_thread
        self._keyboard_paths = keyboard_paths
        self.screens = Screens(board.LCD_WIDTH, board.LCD_HEIGHT)
        self.running = True

        self.lock = threading.RLock()
        self.mode = MODE_MENU
        self.menu_index = MENU_TOGGLE_BLE
        self.ssid_buffer = ""
        self.password_buffer = ""
        self.status_line = ""
        self.busy = False

        self.networks: list[network.Network] = []
        self.scan_index = 0
        self.scan_window = 0
        self.scan_wide = False
        self.password_return = MODE_SCAN
        self.password_known_secured = False
        self.connect_ssid = ""
        self.connect_started_at = 0.0
        self.result_ok = False
        self.result_message = ""

        self.ble_status = BleStatus()
        self.wifi_ssid = ""
        self.wifi_ip = ""
        self.keyboard_ready = False

        self.device = ""
        self.can_scan_wide = False

        self._last_view = None
        self._last_poll_at = 0.0
        self._button_down_at = 0.0

        board.on_button_press(self._on_press)
        board.on_button_release(self._on_release)
        if hasattr(board, "on_exit_request"):
            board.on_exit_request(self._on_exit)
        if hasattr(board, "on_focus_revoked"):
            board.on_focus_revoked(self._on_revoked)

    # ---------- startup ----------

    def probe(self):
        """What this process may do, asked once. Logged, because a missing
        permission otherwise shows up only as a scan that never changes."""
        self.commands.probe_sudo()
        self.device = network.wifi_device(
            output_of(self.commands.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], timeout=10))
        )
        self.can_scan_wide = self.commands.can_sudo or network.scan_permitted(output_of(
            self.commands.run(["nmcli", "-t", "-f", "permission,value", "general", "permissions"],
                              timeout=10)
        ))
        print(f"[connectwifi] wifi device={self.device or 'none'} sudo={self.commands.can_sudo} "
              f"scan={self.can_scan_wide}", flush=True)

    # ---------- lifecycle ----------

    def _on_exit(self, _payload=None):
        self.running = False

    def _on_revoked(self, _payload=None):
        self.running = False

    # ---------- button ----------

    def _on_press(self):
        self._button_down_at = time.time()

    def _on_release(self):
        held = time.time() - self._button_down_at if self._button_down_at else 0.0
        self._button_down_at = 0.0
        self.handle_button(held >= LONG_PRESS_SEC)

    def handle_button(self, long_press: bool):
        with self.lock:
            if self.busy:
                return
            if self.mode == MODE_MENU:
                if long_press:
                    self._activate_menu_item()
                else:
                    self.menu_index = (self.menu_index + 1) % MENU_COUNT
                return
            if self.mode == MODE_SCAN:
                if long_press:
                    self._activate_scan_row()
                else:
                    self._move_scan(1)
                return
            if self.mode == MODE_RESULT:
                self._dismiss_result()
                return
            if self.mode == MODE_CONNECTING:
                return
            if long_press:
                self._cancel_entry()

    # ---------- keyboard ----------

    def handle_key(self, action):
        with self.lock:
            if self.busy:
                return
            if self.mode == MODE_MENU:
                self._menu_key(action)
            elif self.mode == MODE_SCAN:
                self._scan_key(action)
            elif self.mode == MODE_RESULT:
                self._dismiss_result()
            elif self.mode == MODE_CONNECTING:
                return
            else:
                self._entry_key(action)

    def _menu_key(self, action):
        if action == "up":
            self.menu_index = (self.menu_index - 1) % MENU_COUNT
        elif action == "down":
            self.menu_index = (self.menu_index + 1) % MENU_COUNT
        elif action == "submit":
            self._activate_menu_item()
        elif action == "cancel":
            self.running = False

    def _scan_key(self, action):
        if action == "up":
            self._move_scan(-1)
        elif action == "down":
            self._move_scan(1)
        elif action == "submit":
            self._activate_scan_row()
        elif action == "cancel":
            self._leave_scan()

    def _entry_key(self, action):
        if action == "cancel":
            self._cancel_entry()
            return
        if action == "backspace":
            if self.mode == MODE_SSID:
                self.ssid_buffer = self.ssid_buffer[:-1]
            else:
                self.password_buffer = self.password_buffer[:-1]
            return
        if action == "submit":
            self._submit_entry()
            return
        if isinstance(action, tuple) and len(action) == 2 and action[0] == "char":
            if self.mode == MODE_SSID:
                if len(self.ssid_buffer) < SSID_MAX_LEN:
                    self.ssid_buffer += action[1]
            elif len(self.password_buffer) < PASSWORD_MAX_LEN:
                self.password_buffer += action[1]

    # ---------- navigation ----------

    def _scan_row_count(self) -> int:
        return len(SCAN_LEADING) + len(self.networks) + 1  # + the trailing rescan

    def _move_scan(self, delta: int):
        total = self._scan_row_count()
        self.scan_index = (self.scan_index + delta) % total
        if self.scan_index < self.scan_window:
            self.scan_window = self.scan_index
        elif self.scan_index >= self.scan_window + VISIBLE_SCAN_ROWS:
            self.scan_window = self.scan_index - VISIBLE_SCAN_ROWS + 1
        self.scan_window = max(0, min(self.scan_window, max(0, total - VISIBLE_SCAN_ROWS)))

    def _leave_scan(self):
        self.mode = MODE_MENU
        self.status_line = ""

    def _activate_menu_item(self):
        if self.menu_index == MENU_TOGGLE_BLE:
            if not self.ble_status.installed:
                self.status_line = "BLE: run the installer to add it"
                return
            self.status_line = "Working..."
            self._spawn(self._toggle_ble)
        elif self.menu_index == MENU_SCAN:
            self.mode = MODE_SCAN
            self.scan_index = len(SCAN_LEADING) if self.networks else 0
            self.scan_window = 0
            self.status_line = "Checking nearby..."
            self._spawn(lambda: self._scan(wide=False))
        elif self.menu_index == MENU_EXIT:
            self.running = False

    def _activate_scan_row(self):
        index = self.scan_index
        if index == 0:
            self._leave_scan()
            return
        if index == 1:
            self.mode = MODE_SSID
            self.ssid_buffer = ""
            self.password_buffer = ""
            self.password_return = MODE_SSID
            self.status_line = "Type the network name"
            return
        position = index - len(SCAN_LEADING)
        if position >= len(self.networks):
            self.status_line = "Scanning wider range..."
            self._spawn(lambda: self._scan(wide=True))
            return
        chosen = self.networks[position]
        self.ssid_buffer = chosen.ssid
        self.password_buffer = ""
        if chosen.is_open:
            self._start_connect(chosen.ssid, "")
            return
        self.password_return = MODE_SCAN
        self.password_known_secured = True
        self.mode = MODE_PASSWORD
        self.status_line = ""

    def _dismiss_result(self):
        self.mode = MODE_MENU if self.result_ok or not self.networks else MODE_SCAN
        self.result_message = ""
        self.status_line = ""

    def _cancel_entry(self):
        if self.mode == MODE_PASSWORD:
            self.password_buffer = ""
            self.mode = self.password_return
            self.status_line = "" if self.mode == MODE_SCAN else "Type the network name"
            return
        self.mode = MODE_SCAN if self.networks else MODE_MENU
        self.ssid_buffer = ""
        self.password_buffer = ""
        self.status_line = ""

    def _security_of(self, ssid: str) -> str:
        for known in self.networks:
            if known.ssid == ssid:
                return known.security
        return ""

    def _submit_entry(self):
        if self.mode == MODE_SSID:
            if not self.ssid_buffer.strip():
                self.status_line = "SSID cannot be empty"
                return
            self.mode = MODE_PASSWORD
            self.password_return = MODE_SSID
            self.password_known_secured = False
            self.password_buffer = ""
            self.status_line = "Blank = open network"
            return
        ssid = self.ssid_buffer.strip()
        # A blank password is allowed: it reconnects a network NetworkManager
        # already has a profile for. A short one can only fail, and nmcli
        # takes a while to say so, so catch it here. WEP keys are shorter.
        if 0 < len(self.password_buffer) < WPA_MIN_LEN and "wep" not in self._security_of(ssid).lower():
            self.status_line = f"Password needs {WPA_MIN_LEN}+ characters"
            return
        self._start_connect(ssid, self.password_buffer)

    # ---------- workers ----------

    def _spawn(self, target):
        with self.lock:
            if self.busy:
                return
            self.busy = True
        self._start_thread(lambda: self._run_worker(target))

    def _run_worker(self, target):
        try:
            target()
        except Exception as exc:
            with self.lock:
                self.status_line = str(exc)[:60]
        finally:
            with self.lock:
                self.busy = False
                self._last_poll_at = 0.0

    def _toggle_ble(self):
        start = not self.ble.status().active
        result = self.ble.set_running(start)
        with self.lock:
            if result.returncode == 0:
                self.status_line = "BLE started" if start else "BLE stopped"
            else:
                self.status_line = self.commands.short_error(
                    (result.stderr or result.stdout or "Failed").strip()
                )[:60]

    def _start_connect(self, ssid: str, password: str):
        self.mode = MODE_CONNECTING
        self.connect_ssid = ssid
        self.connect_started_at = time.time()
        self.password_buffer = ""
        self.status_line = ""
        self._spawn(lambda: self._connect(ssid, password))

    def _list(self, rescan: str, timeout: float) -> list[network.Network]:
        result = self.commands.run_privileged(network.list_command(self.device, rescan), timeout=timeout)
        return network.parse_networks(output_of(result))

    def _sweep(self) -> list[network.Network]:
        """A real sweep when allowed. Without permission NetworkManager would
        answer a sweep from its cache anyway, so ask for the cache outright."""
        if self.can_scan_wide:
            return self._list("yes", timeout=45)
        return self._list("no", timeout=20)

    def _scan(self, wide: bool):
        """Two tiers. The first pass reads NetworkManager's cached scan: no
        radio sweep, so it costs no power and lands instantly, then keeps only
        what is comfortably in range. Rescan forces a real sweep and shows
        everything. An empty first pass escalates on its own, so entering the
        list never dead-ends on a cold or stale cache."""
        if wide:
            found = self._sweep()
        else:
            found = [n for n in self._list("no", timeout=20) if n.signal >= NEARBY_MIN_SIGNAL]
            # Nothing to choose from - only the network we are already on, or
            # nothing at all - means the cache is too cold to be useful.
            if not any(not n.active for n in found):
                wide = True
                found = self._sweep()

        with self.lock:
            self.networks = found
            self.scan_wide = wide
            if found:
                if not wide:
                    self.status_line = f"{len(found)} nearby"
                elif self.can_scan_wide:
                    self.status_line = f"{len(found)} networks"
                else:
                    self.status_line = f"{len(found)} cached (no scan permission)"
                self.scan_index = len(SCAN_LEADING)
            else:
                self.status_line = "No networks found"
                self.scan_index = 0
            self.scan_window = 0
            self._move_scan(0)

    def _ssid_visible(self, ssid: str) -> bool:
        with self.lock:
            if self.networks:
                return any(known.ssid == ssid for known in self.networks)
        result = self.commands.run(network.list_command(self.device, "no", fields="SSID"), timeout=10)
        if result.returncode != 0:
            return True
        return network.ssid_listed(result.stdout, ssid)

    def _connect(self, ssid: str, password: str):
        args = network.connect_command(ssid, password, self.device, hidden=not self._ssid_visible(ssid))
        result = self.commands.run_privileged(args, timeout=90)
        ok = result.returncode == 0
        message = (result.stdout if ok else (result.stderr or result.stdout)).strip()
        with self.lock:
            self.result_ok = ok
            self.result_message = (message if ok else self.commands.short_error(message or "Failed")) \
                or ("Connected" if ok else "Failed")
            self.mode = MODE_RESULT
            self.status_line = ""
            if ok:
                self.ssid_buffer = ""

    # ---------- status ----------

    def _wifi_state(self) -> tuple[str, str]:
        if not self.device:
            self.device = network.wifi_device(
                output_of(self.commands.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], timeout=5))
            )
        ssid = network.active_ssid(output_of(
            self.commands.run(network.list_command(self.device, "no", fields="ACTIVE,SSID"), timeout=5)
        ))
        ipv4 = ""
        if ssid and self.device:
            ipv4 = network.ipv4_address(output_of(self.commands.run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", self.device], timeout=5
            )))
        return ssid, ipv4

    def poll_status(self):
        ble_status = self.ble.status()
        ssid, ipv4 = self._wifi_state()
        keyboard_ready = bool(self._keyboard_paths())
        with self.lock:
            self.ble_status = ble_status
            self.wifi_ssid = ssid
            self.wifi_ip = ipv4
            self.keyboard_ready = keyboard_ready

    # ---------- rendering ----------

    def view(self) -> View:
        with self.lock:
            connecting = self.mode == MODE_CONNECTING
            return View(
                mode=self.mode,
                menu_index=self.menu_index,
                ssid=self.ssid_buffer,
                password_len=len(self.password_buffer),
                status=self.status_line,
                busy=self.busy,
                ble_installed=self.ble_status.installed,
                ble_active=self.ble_status.active,
                ble_name=self.ble_status.name,
                ble_key=self.ble_status.key,
                wifi_ssid=self.wifi_ssid,
                wifi_ip=self.wifi_ip,
                keyboard_ready=self.keyboard_ready,
                password_known_secured=self.password_known_secured,
                scan_index=self.scan_index,
                scan_window=self.scan_window,
                scan_wide=self.scan_wide,
                networks=tuple((n.ssid, n.signal, n.active, n.is_open) for n in self.networks),
                connect_ssid=self.connect_ssid,
                result_ok=self.result_ok,
                result_message=self.result_message,
                phase=int(time.time() * 8) % 24 if connecting else 0,
                elapsed=int(time.time() - self.connect_started_at)
                if connecting and self.connect_started_at else 0,
            )

    def render(self, force: bool = False):
        view = self.view()
        if not force and view == self._last_view:
            return
        self._last_view = view
        image = self.screens.render(view)
        self.board.draw_image(0, 0, image.width, image.height, rgb565_bytes(image))

    # ---------- main loop ----------

    def run(self):
        self.probe()
        if self.keyboard is not None:
            self.keyboard.start(self.handle_key)
        try:
            while self.running:
                now = time.time()
                if now - self._last_poll_at >= POLL_INTERVAL_SEC:
                    self._last_poll_at = now
                    self.poll_status()
                self.render()
                time.sleep(FRAME_INTERVAL_SEC)
        finally:
            if self.keyboard is not None:
                self.keyboard.stop()
            self.board.cleanup()


def main():
    from connectwifi.board import create_board

    ConnectWifiApp(create_board(), keyboard=KeyboardReader()).run()
