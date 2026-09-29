"""Draw every screen. Set CONNECTWIFI_SCREENSHOTS=dir to keep the PNGs for
a look by eye."""

import os

import pytest

from connectwifi.screens import (
    MODE_CONNECTING, MODE_MENU, MODE_PASSWORD, MODE_RESULT, MODE_SCAN, MODE_SSID,
    Screens, View, ble_menu_label, rgb565_bytes, scan_rows,
)

NETWORKS = (
    ("Home", 81, True, False),
    ("Cafe Guest With A Very Long Network Name", 66, False, True),
    ("Office", 55, False, False),
    ("Neighbour", 44, False, False),
    ("Garage", 41, False, False),
)
BASE = View(ble_installed=True, ble_active=True, ble_name="orangepizero2w", ble_key="k7m2x9qa",
            wifi_ssid="Home", wifi_ip="192.168.0.130", keyboard_ready=True, networks=NETWORKS)

SCREENS = {
    "menu": BASE,
    "menu-no-ble": View(wifi_ssid="", keyboard_ready=False),
    "scan": View(**{**BASE.__dict__, "mode": MODE_SCAN, "scan_index": 3, "status": "5 nearby"}),
    "scan-scrolled": View(**{**BASE.__dict__, "mode": MODE_SCAN, "scan_index": 7, "scan_window": 2,
                             "scan_wide": True}),
    "ssid": View(**{**BASE.__dict__, "mode": MODE_SSID, "ssid": "Lab:5G", "status": "Type the network name"}),
    "password": View(**{**BASE.__dict__, "mode": MODE_PASSWORD, "ssid": "Office", "password_len": 9,
                        "password_known_secured": True}),
    "connecting": View(**{**BASE.__dict__, "mode": MODE_CONNECTING, "connect_ssid": "Office", "phase": 9,
                          "elapsed": 4}),
    "result-ok": View(**{**BASE.__dict__, "mode": MODE_RESULT, "connect_ssid": "Office", "result_ok": True,
                         "result_message": "Device 'wlan0' successfully activated with "
                                           "'5f1c0e5e-3b8a-4d0e-9d7e-1f6f2a1c9b10'."}),
    "result-failed": View(**{**BASE.__dict__, "mode": MODE_RESULT, "connect_ssid": "Office",
                             "result_message": "Connection activation failed: Secrets were required, "
                                               "but not provided."}),
}


@pytest.fixture(scope="module")
def screens():
    return Screens()


@pytest.mark.parametrize("name", sorted(SCREENS))
def test_screen_draws(screens, name):
    image = screens.render(SCREENS[name])
    assert image.size == (240, 280)
    # Something besides the background was drawn below the header.
    assert len(set(image.crop((0, 50, 240, 240)).getdata())) > 2
    folder = os.environ.get("CONNECTWIFI_SCREENSHOTS")
    if folder:
        os.makedirs(folder, exist_ok=True)
        image.save(os.path.join(folder, f"{name}.png"))


def test_rgb565_is_big_endian_565():
    from PIL import Image

    image = Image.new("RGB", (2, 1))
    image.putpixel((0, 0), (255, 0, 0))
    image.putpixel((1, 0), (0, 0, 255))
    assert rgb565_bytes(image) == bytes([0xF8, 0x00, 0x00, 0x1F])


def test_scan_rows_frame_the_networks_with_fixed_rows():
    rows = scan_rows(View(networks=NETWORKS[:2]))
    assert rows[0] == ("Back", "") and rows[1] == ("Type hidden network...", "")
    assert rows[2] == ("Home", "now") and rows[3][1] == "66% open"
    assert rows[-1] == ("Rescan wider range", "")
    assert scan_rows(View(scan_wide=True))[-1] == ("Rescan", "")


def test_ble_menu_label():
    assert ble_menu_label(View()) == "BLE not installed"
    assert ble_menu_label(View(ble_installed=True)) == "Turn BLE on"
    assert ble_menu_label(View(ble_installed=True, ble_active=True)) == "Turn BLE off"
