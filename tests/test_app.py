import pytest
from conftest import FakeRunner, done

from connectwifi import app as appmod
from connectwifi.screens import (
    MENU_EXIT, MENU_SCAN, MENU_TOGGLE_BLE, MODE_CONNECTING, MODE_MENU, MODE_PASSWORD,
    MODE_RESULT, MODE_SCAN, MODE_SSID, VISIBLE_SCAN_ROWS,
)
from connectwifi.system import Commands

CACHED = "*:Home:70:WPA2\n :Cafe:65:\n :Office:55:WPA2 WPA3\n :Far:20:WPA2\n"
SWEPT = CACHED + " :Distant:12:WPA2\n"
BLE_ON = ("LoadState=loaded\nActiveState=active\n"
          "ExecStart={ path=/x ; argv[]=/x --name opi --key k7m2 --config c.json ; }\n")
BLE_OFF = BLE_ON.replace("ActiveState=active", "ActiveState=inactive")
BLE_MISSING = "LoadState=not-found\nActiveState=inactive\nExecStart=\n"


class FakeBoard:
    LCD_WIDTH = 240
    LCD_HEIGHT = 280

    def __init__(self):
        self.frames = []
        self.cleaned_up = False

    def on_button_press(self, callback):
        self.press = callback

    def on_button_release(self, callback):
        self.release = callback

    def on_exit_request(self, callback):
        self.exit_request = callback

    def on_focus_revoked(self, callback):
        self.revoked = callback

    def draw_image(self, x, y, width, height, data):
        self.frames.append((x, y, width, height, len(data)))

    def cleanup(self):
        self.cleaned_up = True


def is_list(rescan, fields="IN-USE,SSID,SIGNAL,SECURITY"):
    return lambda a: a[:2] != ["sudo", "-n"] and "list" in a and fields in a and a[-1] == rescan


def fake_nm(cached=CACHED, swept=SWEPT, scan="yes", ble=BLE_OFF):
    return FakeRunner(
        (lambda a: a == ["sudo", "-n", "true"], done(returncode=1, stderr="sudo: a password is required")),
        (lambda a: a[:2] == ["systemctl", "show"], done(ble)),
        (lambda a: "DEVICE,TYPE" in a, done("lo:loopback\np2p-dev-wlan0:wifi-p2p\nwlan0:wifi\n")),
        (lambda a: "general" in a, done(f"org.freedesktop.NetworkManager.wifi.scan:{scan}\n")),
        (is_list("no"), done(cached)),
        (is_list("yes"), done(swept)),
        (is_list("no", "SSID"), done("\n".join(line.split(":")[1] for line in swept.splitlines()))),
        (is_list("no", "ACTIVE,SSID"), done("yes:Home\nno:Cafe\n")),
        (lambda a: "IP4.ADDRESS" in a, done("IP4.ADDRESS[1]:192.168.0.130/24\n")),
        (lambda a: "connect" in a, done("Device 'wlan0' successfully activated with 'abc'.\n")),
    )


def make_app(runner=None, keyboards=("/dev/input/event1",)):
    runner = runner or fake_nm()
    board = FakeBoard()
    app = appmod.ConnectWifiApp(board, commands=Commands(runner), start_thread=lambda target: target(),
                                keyboard_paths=lambda: list(keyboards))
    app.probe()
    app.poll_status()
    return app, runner, board


def type_text(app, text):
    for char in text:
        app.handle_key(("char", char))


def open_scan(app):
    app.menu_index = MENU_SCAN
    app.handle_button(long_press=True)


def select_network(app, ssid):
    app.scan_index = 2 + [n.ssid for n in app.networks].index(ssid)
    app.handle_button(long_press=True)


def test_probe_finds_the_wifi_device_and_scan_permission():
    app, _, _ = make_app()
    assert app.device == "wlan0" and app.can_scan_wide and not app.commands.can_sudo
    assert (app.wifi_ssid, app.wifi_ip) == ("Home", "192.168.0.130")
    assert app.ble_status.installed and not app.ble_status.active and app.keyboard_ready


def test_status_polling_never_sweeps_the_radio():
    app, runner, _ = make_app()
    for _ in range(3):
        app.poll_status()
    lists = runner.calls_with("list")
    assert lists and all(call[-2:] == ["--rescan", "no"] for call in lists)


def test_short_press_steps_and_wraps_the_menu():
    app, _, _ = make_app()
    assert app.menu_index == MENU_TOGGLE_BLE
    for expected in (MENU_SCAN, MENU_EXIT, MENU_TOGGLE_BLE):
        app.handle_button(long_press=False)
        assert app.menu_index == expected


def test_first_scan_shows_only_what_is_nearby_from_the_cache():
    app, runner, _ = make_app()
    open_scan(app)
    assert app.mode == MODE_SCAN and not app.scan_wide
    assert [n.ssid for n in app.networks] == ["Home", "Cafe", "Office"]   # Far is under 40%
    assert app.scan_index == 2 and app.status_line == "3 nearby"
    assert not runner.calls_with("--rescan", "yes")


def test_a_cold_cache_escalates_to_a_real_sweep():
    app, runner, _ = make_app(fake_nm(cached="*:Home:70:WPA2\n"))
    open_scan(app)
    assert app.scan_wide and len(runner.calls_with("--rescan", "yes")) == 1
    assert "Distant" in [n.ssid for n in app.networks]


def test_a_cold_cache_without_scan_permission_does_not_ask_for_a_sweep():
    app, runner, _ = make_app(fake_nm(cached="*:Home:70:WPA2\n", scan="auth"))
    open_scan(app)
    assert app.scan_wide and "cached" in app.status_line
    assert not runner.calls_with("--rescan", "yes")


def test_rescan_without_permission_says_the_list_is_cached():
    app, runner, _ = make_app(fake_nm(scan="auth"))
    open_scan(app)
    app.scan_index = 2 + len(app.networks)          # the trailing Rescan row
    app.handle_button(long_press=True)
    assert app.scan_wide and "cached" in app.status_line
    assert not runner.calls_with("--rescan", "yes")


def test_an_open_network_connects_at_once():
    app, runner, _ = make_app()
    open_scan(app)
    select_network(app, "Cafe")
    assert runner.calls_with("connect")[-1] == ["nmcli", "device", "wifi", "connect", "Cafe",
                                                "ifname", "wlan0"]
    assert app.mode == MODE_RESULT and app.result_ok


def test_a_secured_network_asks_for_the_password_then_connects():
    app, runner, _ = make_app()
    open_scan(app)
    select_network(app, "Office")
    assert app.mode == MODE_PASSWORD and app.password_known_secured
    type_text(app, "hunter22x")
    app.handle_key("backspace")
    app.handle_key("submit")
    assert runner.calls_with("connect")[-1] == ["nmcli", "device", "wifi", "connect", "Office",
                                                "password", "hunter22", "ifname", "wlan0"]
    assert app.mode == MODE_RESULT and app.result_ok and app.password_buffer == ""
    app.handle_button(long_press=False)
    assert app.mode == MODE_MENU


def test_a_wpa_password_under_8_characters_is_caught_before_nmcli():
    app, runner, _ = make_app()
    open_scan(app)
    select_network(app, "Office")
    type_text(app, "short77")
    app.handle_key("submit")
    assert app.mode == MODE_PASSWORD and "8+" in app.status_line
    assert not runner.calls_with("connect")


def test_a_blank_password_reuses_a_saved_profile():
    app, runner, _ = make_app()
    open_scan(app)
    select_network(app, "Office")
    app.handle_key("submit")
    assert "password" not in runner.calls_with("connect")[-1]


def test_a_hidden_network_is_typed_in_and_flagged_hidden():
    app, runner, _ = make_app()
    open_scan(app)
    app.scan_index = 1
    app.handle_button(long_press=True)
    assert app.mode == MODE_SSID
    app.handle_key("submit")
    assert app.status_line == "SSID cannot be empty"
    type_text(app, "Lab:5G")
    app.handle_key("submit")
    assert app.mode == MODE_PASSWORD and not app.password_known_secured
    type_text(app, "labpass1")
    app.handle_key("submit")
    assert runner.calls_with("connect")[-1][-2:] == ["hidden", "yes"]


def test_a_failed_connect_explains_and_returns_to_the_list():
    runner = fake_nm()
    runner.add(lambda a: "connect" in a,
               done(returncode=4, stderr="Error: Connection activation failed: Secrets were required, "
                                         "but not provided.\n"))
    app, _, _ = make_app(runner)
    open_scan(app)
    select_network(app, "Office")
    type_text(app, "wrongpass")
    app.handle_key("submit")
    assert app.mode == MODE_RESULT and not app.result_ok
    assert app.result_message.startswith("Connection activation failed")
    app.handle_key("submit")
    assert app.mode == MODE_SCAN


def test_not_authorized_points_at_the_installer():
    runner = fake_nm()
    runner.add(lambda a: "connect" in a,
               done(returncode=1, stderr="Error: Not authorized to control networking.\n"))
    app, _, _ = make_app(runner)
    open_scan(app)
    select_network(app, "Cafe")
    assert app.result_message == "Not allowed - run the installer again"


def test_ble_switch_uses_sudo_and_reports():
    app, runner, _ = make_app()
    app.handle_button(long_press=True)           # menu item 0: Turn BLE on
    assert runner.calls[-1] == ["sudo", "-n", "systemctl", "start", "sugar-wifi-config.service"]
    assert app.status_line == "BLE started"

    runner.add(lambda a: a[:2] == ["systemctl", "show"], done(BLE_ON))
    app.poll_status()
    app.handle_button(long_press=True)
    assert runner.calls[-1][3] == "stop"


def test_ble_switch_without_the_service_says_so_and_runs_nothing():
    app, runner, _ = make_app(fake_nm(ble=BLE_MISSING))
    before = len(runner.calls)
    app.handle_button(long_press=True)
    assert "installer" in app.status_line and len(runner.calls) == before


def test_input_is_ignored_while_a_worker_runs():
    app, _, _ = make_app()
    app.busy = True
    app.handle_button(long_press=False)
    app.handle_key("down")
    assert app.menu_index == MENU_TOGGLE_BLE


def test_long_lists_scroll_and_wrap():
    many = "".join(f" :Net{i:02d}:{90 - i}:WPA2\n" for i in range(12))
    app, _, _ = make_app(fake_nm(cached=many, swept=many))
    open_scan(app)
    total = 2 + 12 + 1
    for _ in range(VISIBLE_SCAN_ROWS + 2):
        app.handle_key("down")
    assert app.scan_index == 2 + VISIBLE_SCAN_ROWS + 2
    assert app.scan_window == app.scan_index - VISIBLE_SCAN_ROWS + 1
    app.scan_index, app.scan_window = total - 1, total - VISIBLE_SCAN_ROWS
    app.handle_key("down")
    assert (app.scan_index, app.scan_window) == (0, 0)
    app.handle_key("up")
    assert app.scan_index == total - 1 and app.scan_window == total - VISIBLE_SCAN_ROWS


def test_escape_steps_back_and_leaves_from_the_menu():
    app, _, _ = make_app()
    open_scan(app)
    select_network(app, "Office")
    app.handle_key("cancel")
    assert app.mode == MODE_SCAN
    app.handle_key("cancel")
    assert app.mode == MODE_MENU
    app.handle_key("cancel")
    assert not app.running


def test_hold_cancels_typing():
    app, _, _ = make_app()
    open_scan(app)
    select_network(app, "Office")
    type_text(app, "abc")
    app.handle_button(long_press=False)
    assert app.mode == MODE_PASSWORD
    app.handle_button(long_press=True)
    assert app.mode == MODE_SCAN and app.password_buffer == ""


def test_long_press_timing_comes_from_the_button_events(monkeypatch):
    app, _, board = make_app()
    clock = iter([100.0, 100.3, 200.0, 201.2])
    monkeypatch.setattr(appmod.time, "time", lambda: next(clock))
    board.press()
    board.release()                             # 0.3 s: next
    assert app.menu_index == MENU_SCAN
    board.press()
    board.release()                             # 1.2 s: select
    assert app.mode == MODE_SCAN


def test_the_exit_gesture_stops_the_loop_and_releases_the_screen():
    app, _, board = make_app()
    board.exit_request()
    assert not app.running
    app.run()
    assert board.cleaned_up


@pytest.mark.parametrize("mode", [MODE_MENU, MODE_SCAN, MODE_SSID, MODE_PASSWORD, MODE_CONNECTING, MODE_RESULT])
def test_every_mode_draws_a_full_frame_once(mode, monkeypatch):
    app, _, board = make_app()
    open_scan(app)
    app.mode = mode
    # The connecting animation follows the clock; hold it still.
    monkeypatch.setattr(appmod.time, "time", lambda: 1000.0)
    app.connect_started_at = 990.0
    app.render()
    app.render()
    assert board.frames == [(0, 0, 240, 280, 240 * 280 * 2)]
