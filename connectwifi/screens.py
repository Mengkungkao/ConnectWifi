"""MFruit OS-styled screens for the Wi-Fi manager.

The connection workflow stays independent of drawing: ``render`` receives an
immutable ``View`` and returns one Pillow image for the Whisplay framebuffer.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from mfruit_sdk.status import Status
from mfruit_sdk.ui import (Canvas, DARK, Row, draw_list, footer, message,
                           status_bar, text_field, toast, to_rgb565)
from mfruit_sdk.ui.theme import CONTENT_BOTTOM, CONTENT_TOP, MARGIN, SCREEN_W

MODE_MENU = "menu"
MODE_SCAN = "scan"
MODE_SSID = "ssid"
MODE_PASSWORD = "password"
MODE_CONNECTING = "connecting"
MODE_RESULT = "result"

# The main page is the Wi-Fi section of Settings: common actions first, then
# setup alternatives and a predictable way back to MFruit OS.
MENU_SCAN = 0
MENU_HIDDEN = 1
MENU_TOGGLE_BLE = 2
MENU_EXIT = 3
MENU_COUNT = 4

# Fixed rows around the scanned networks, in list order. Hidden network stays
# available here as well as on the main page so it is never hard to find.
SCAN_LEADING = ("Back", "Type hidden network...")
VISIBLE_SCAN_ROWS = 5


@dataclass(frozen=True)
class View:
    """Everything a frame shows. Equal views produce equal frames."""

    mode: str = MODE_MENU
    menu_index: int = 0
    ssid: str = ""
    password_len: int = 0
    status: str = ""
    busy: bool = False
    ble_installed: bool = False
    ble_active: bool = False
    ble_name: str = ""
    ble_key: str = ""
    wifi_ssid: str = ""
    wifi_ip: str = ""
    wifi_level: int | None = None
    battery: int | None = None
    charging: bool = False
    keyboard_ready: bool = False
    button_down: bool = False
    hold_armed: bool = False
    password_known_secured: bool = False
    scan_index: int = 0
    scan_window: int = 0
    scan_wide: bool = False
    # (ssid, signal, active, is_open, saved) per network
    networks: tuple = ()
    connect_ssid: str = ""
    result_ok: bool = False
    result_message: str = ""
    phase: int = 0
    elapsed: int = 0


def rgb565_bytes(image: Image.Image) -> bytes:
    """Compatibility name used by the app and older integrations."""
    return to_rgb565(image)


def scan_rows(view: View) -> list[tuple[str, str]]:
    """Return the public label/meta representation of the network list."""
    rows = [(name, "") for name in SCAN_LEADING]
    for ssid, signal, active, is_open, saved in view.networks:
        if active:
            meta = "now"
        elif saved:
            meta = f"{signal}% saved"
        else:
            meta = f"{signal}% open" if is_open else f"{signal}%"
        rows.append((ssid, meta))
    rows.append(("Rescan" if view.scan_wide else "Rescan wider range", ""))
    rows.append(("Back to Settings", ""))
    return rows


def ble_menu_label(view: View) -> str:
    if not view.ble_installed:
        return "BLE not installed"
    return "Turn BLE off" if view.ble_active else "Turn BLE on"


class Screens:
    def __init__(self, width: int = 240, height: int = 280, theme=DARK):
        self.width = width
        self.height = height
        self.theme = theme

    def render(self, view: View) -> Image.Image:
        c = Canvas(self.theme, size=(self.width, self.height))
        device = Status(view.wifi_level, view.battery, view.charging)
        dot = self._state_color(c, view)

        if view.mode == MODE_MENU:
            self._menu(c, view, device, dot)
        elif view.mode == MODE_SCAN:
            self._scan(c, view, device, dot)
        elif view.mode == MODE_CONNECTING:
            self._connecting(c, view, device)
        elif view.mode == MODE_RESULT:
            self._result(c, view, device)
        else:
            self._entry(c, view, device)
        return c.image

    @staticmethod
    def _state_color(c: Canvas, view: View):
        if view.busy:
            return c.theme.accent
        if view.wifi_ssid:
            return c.theme.success
        return c.theme.warning

    def _menu(self, c: Canvas, view: View, device: Status, dot) -> None:
        t = c.theme
        status_bar(c, "Wi-Fi", device, dot=dot)

        # A compact connection card replaces the separate MFruit status page.
        c.rounded((MARGIN - 4, CONTENT_TOP, SCREEN_W - MARGIN + 4, 96), 12,
                  fill=t.surface)
        connected = bool(view.wifi_ssid)
        phone_selected = view.menu_index == MENU_TOGGLE_BLE and view.ble_installed
        heading = ("PHONE SETUP ON" if view.ble_active else "PHONE SETUP OFF") if phone_selected else (
            "CONNECTED" if connected else "NOT CONNECTED")
        title = view.ble_name or "Phone setup" if phone_selected else (
            view.wifi_ssid or "Choose a network below")
        c.text(MARGIN + 6, 42, heading, 11,
               "bold", t.accent if phone_selected else t.success if connected else t.warning)
        c.text(MARGIN + 6, 58, title, 16,
               "semibold", t.text, max_width=SCREEN_W - 2 * MARGIN - 12)
        detail = view.wifi_ip or ("No IP address" if connected else "Wi-Fi is available")
        if phone_selected:
            detail = f"Key: {view.ble_key}" if view.ble_key else "No setup key configured"
        c.text(MARGIN + 6, 78, detail, 12, "regular", t.text_muted,
               max_width=SCREEN_W - 2 * MARGIN - 12)

        phone_detail = "On · hold to stop" if view.ble_active else "Off · hold to start"
        rows = [
            Row("Choose a network", kind="nav"),
            Row("Hidden network", kind="nav"),
            Row("Phone setup", subtitle=phone_detail, value=view.ble_active, kind="toggle",
                enabled=view.ble_installed),
            Row("Back to Settings", kind="back"),
        ]
        # Keep an unavailable service explanatory instead of showing a false toggle.
        if not view.ble_installed:
            rows[2] = Row("Phone setup", subtitle="Run the installer to add it",
                          value="Unavailable", kind="info", tone="muted")
        draw_list(c, rows, view.menu_index, top=100, bottom=CONTENT_BOTTOM)
        action = "back" if view.menu_index == MENU_EXIT else "select"
        footer(c, self._list_hints(view, action))
        if view.status:
            toast(c, view.status, "warning" if "failed" in view.status.lower() else "")

    def _scan(self, c: Canvas, view: View, device: Status, dot) -> None:
        t = c.theme
        badge = ("SCAN", t.accent) if view.busy else None
        status_bar(c, "Networks", device, badge=badge, dot=dot)
        if view.status:
            c.text(MARGIN + 2, 43, view.status, 12, "medium", t.text_muted,
                   max_width=SCREEN_W - 2 * MARGIN - 4)
        rows = [Row("Back", kind="back"), Row("Hidden network", kind="nav")]
        for ssid, signal, active, is_open, saved in view.networks:
            if active:
                subtitle, tone = "Connected", "success"
            elif saved:
                subtitle, tone = "Saved", ""
            elif is_open:
                subtitle, tone = "Open network", "warning"
            else:
                subtitle, tone = "Password required", ""
            rows.append(Row(ssid, subtitle=subtitle, value=f"{signal}%", kind="nav", tone=tone))
        rows.append(Row("Rescan" if view.scan_wide else "Rescan wider range", kind="action"))
        rows.append(Row("Back to Settings", kind="back"))
        draw_list(c, rows, view.scan_index, top=58 if view.status else CONTENT_TOP,
                  bottom=CONTENT_BOTTOM)
        action = "back" if view.scan_index in (0, len(rows) - 1) else "select"
        footer(c, self._list_hints(view, action))

    @staticmethod
    def _list_hints(view: View, action: str) -> list:
        if view.busy:
            return [("wait", "working")]
        if view.hold_armed:
            return [("release", f"to {action}")]
        return [("tap", "next"), ("hold", action)]

    def _connecting(self, c: Canvas, view: View, device: Status) -> None:
        t = c.theme
        status_bar(c, "Wi-Fi", device, dot=t.accent)
        message(c, "Connecting", view.connect_ssid or "Network", tone="accent",
                top=CONTENT_TOP, bottom=190)
        left, right, y = MARGIN, SCREEN_W - MARGIN, 181
        c.rounded((left, y, right, y + 8), 4, fill=t.surface_hi)
        segment = max(28, (right - left) // 3)
        travel = (view.phase / 24.0) * ((right - left) + segment) - segment
        x0, x1 = left + max(0, travel), left + min(right - left, travel + segment)
        if x1 > x0:
            c.rounded((x0, y, x1, y + 8), 4, fill=t.accent)
        c.text(SCREEN_W // 2, 204, f"{view.elapsed}s elapsed", 12, "regular",
               t.text_muted, anchor="ma")
        footer(c, [("wait", "NetworkManager is joining")])

    def _result(self, c: Canvas, view: View, device: Status) -> None:
        t = c.theme
        tone = "success" if view.result_ok else "error"
        status_bar(c, "Wi-Fi", device, dot=t.success if view.result_ok else t.error)
        message(c, "Connected" if view.result_ok else "Could not connect",
                self._result_body(view), tone=tone)
        footer(c, [("tap", "continue")])

    @staticmethod
    def _result_body(view: View) -> str:
        parts = [part for part in (view.connect_ssid, view.result_message) if part]
        return "\n".join(parts)

    def _entry(self, c: Canvas, view: View, device: Status) -> None:
        t = c.theme
        entering_ssid = view.mode == MODE_SSID
        title = "Hidden network" if entering_ssid else "Password"
        status_bar(c, title, device, dot=t.warning, title_sizes=(17, 15, 13))
        c.text(MARGIN, 54, "Network name" if entering_ssid else f"For {view.ssid}",
               13, "medium", t.text_muted, max_width=SCREEN_W - 2 * MARGIN)
        if entering_ssid:
            shown, placeholder = view.ssid, "Type the SSID"
        else:
            shown = "•" * min(view.password_len, 24)
            placeholder = "Blank for an open network" if not view.password_known_secured else "Type password"
        text_field(c, shown, 76, placeholder=placeholder)
        if view.status:
            warning = "cannot" in view.status.lower() or "needs" in view.status.lower()
            c.text(MARGIN, 122, view.status, 12, "semibold",
                   t.warning if warning else t.text_muted,
                   max_width=SCREEN_W - 2 * MARGIN)
        keyboard = "Keyboard ready" if view.keyboard_ready else "Connect a USB keyboard to type"
        c.text(MARGIN, 157, keyboard, 13, "medium",
               t.success if view.keyboard_ready else t.warning,
               max_width=SCREEN_W - 2 * MARGIN)
        c.text(MARGIN, 181, "Enter  continue", 12, "regular", t.text_muted)
        c.text(MARGIN, 201, "Esc  back    Backspace  delete", 12, "regular", t.text_muted)
        footer(c, [("hold", "cancel")])
