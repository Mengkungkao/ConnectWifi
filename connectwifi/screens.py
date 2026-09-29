"""Drawing the 240x280 screen. Nothing here touches hardware: render()
turns a View into a PIL image, so every screen can be drawn in tests."""

from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

try:
    import numpy as np
except Exception:
    np = None

MODE_MENU = "menu"
MODE_SCAN = "scan"
MODE_SSID = "ssid"
MODE_PASSWORD = "password"
MODE_CONNECTING = "connecting"
MODE_RESULT = "result"

MENU_TOGGLE_BLE = 0
MENU_SCAN = 1
MENU_EXIT = 2
MENU_COUNT = 3

# Fixed rows around the scanned networks, in list order.
SCAN_LEADING = ("Back", "Type hidden network...")
VISIBLE_SCAN_ROWS = 6

MARGIN = 14
TEXT_X = 24


@dataclass(frozen=True)
class View:
    """Everything a frame shows. Two equal Views draw the same frame, which
    is how the app skips redrawing when nothing changed."""

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
    keyboard_ready: bool = False
    password_known_secured: bool = False
    scan_index: int = 0
    scan_window: int = 0
    scan_wide: bool = False
    # (ssid, signal, active, is_open) per network
    networks: tuple = ()
    connect_ssid: str = ""
    result_ok: bool = False
    result_message: str = ""
    # Drive the sweep animation and the elapsed counter while connecting.
    phase: int = 0
    elapsed: int = 0


def load_font(size: int, bold: bool = False):
    candidates = []
    if bold:
        candidates.append("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    candidates.append("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def rgb565_bytes(image: Image.Image) -> bytes:
    rgb = image.convert("RGB")
    if np is not None:
        pixels = np.asarray(rgb, dtype=np.uint16)
        packed = (
            ((pixels[:, :, 0] & 0xF8) << 8)
            | ((pixels[:, :, 1] & 0xFC) << 3)
            | (pixels[:, :, 2] >> 3)
        )
        return packed.astype(">u2").tobytes()
    out = bytearray()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = rgb.getpixel((x, y))
            value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out.append((value >> 8) & 0xFF)
            out.append(value & 0xFF)
    return bytes(out)


def scan_rows(view: View) -> list[tuple[str, str]]:
    """(label, meta) for every row of the scan list, in order."""
    rows = [(name, "") for name in SCAN_LEADING]
    rows += [
        (ssid, "now" if active else (f"{signal}% open" if is_open else f"{signal}%"))
        for ssid, signal, active, is_open in view.networks
    ]
    rows.append(("Rescan" if view.scan_wide else "Rescan wider range", ""))
    return rows


def ble_menu_label(view: View) -> str:
    if not view.ble_installed:
        return "BLE not installed"
    return "Turn BLE off" if view.ble_active else "Turn BLE on"


class Screens:
    def __init__(self, width: int = 240, height: int = 280):
        self.width = width
        self.height = height
        self.title_font = load_font(20, bold=True)
        self.body_font = load_font(15)
        self.row_font = load_font(14)
        self.small_font = load_font(12)

    def render(self, view: View) -> Image.Image:
        width, height = self.width, self.height
        image = Image.new("RGB", (width, height), (11, 16, 24))
        draw = ImageDraw.Draw(image)

        accent = (60, 190, 100) if view.ble_active else (90, 100, 115)
        draw.rounded_rectangle((MARGIN, 10, width - MARGIN, 44), radius=12, fill=accent)
        draw.text((TEXT_X, 18), "CONNECT WIFI", fill=(255, 255, 255), font=self.title_font)

        if view.mode == MODE_MENU:
            self._menu(draw, view)
        elif view.mode == MODE_SCAN:
            self._scan(draw, view)
        elif view.mode == MODE_CONNECTING:
            self._connecting(draw, view)
        elif view.mode == MODE_RESULT:
            self._result(draw, view)
        else:
            self._entry(draw, view)

        self._footer(draw, view)
        return image

    # ---------- helpers ----------

    def _fit(self, draw, text: str, font, max_width: float) -> str:
        if draw.textlength(text, font=font) <= max_width:
            return text
        while text and draw.textlength(text + "...", font=font) > max_width:
            text = text[:-1]
        return text + "..."

    def _wrap(self, draw, text: str, font, max_width: float, max_lines: int) -> list[str]:
        lines, current = [], ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    break
            else:
                current = candidate
        if current and len(lines) < max_lines:
            lines.append(current)
        return [self._fit(draw, line, font, max_width) for line in lines]

    def _row(self, draw, y: int, label: str, meta: str, selected: bool, dim: bool = False,
             meta_color=None):
        width = self.width
        if selected:
            draw.rounded_rectangle((MARGIN, y, width - MARGIN, y + 26), radius=8, fill=(38, 62, 92))
        meta_width = draw.textlength(meta, font=self.small_font) if meta else 0
        meta_x = width - TEXT_X - meta_width
        draw.text(
            (TEXT_X, y + 6),
            self._fit(draw, label, self.row_font, meta_x - TEXT_X - 8),
            fill=(255, 255, 255) if selected else ((150, 166, 184) if dim else (170, 186, 204)),
            font=self.row_font,
        )
        if meta:
            draw.text((meta_x, y + 9), meta,
                      fill=meta_color or ((140, 190, 240) if selected else (100, 116, 134)),
                      font=self.small_font)

    # ---------- screens ----------

    def _menu(self, draw, view: View):
        width = self.width
        text_width = width - 2 * TEXT_X
        draw.rounded_rectangle((MARGIN, 52, width - MARGIN, 142), radius=12, fill=(20, 28, 40))
        if not view.ble_installed:
            title, color = "NO BLE", (150, 166, 184)
        elif view.ble_active:
            title, color = "BLE ON", (120, 230, 150)
        else:
            title, color = "BLE OFF", (200, 90, 90)
        draw.text((TEXT_X, 58), title, fill=color, font=self.title_font)
        # Fixed line count keeps the card from resizing when the IP appears.
        details = [
            f"Name: {view.ble_name if view.ble_installed else 'not installed'}",
            f"Key:  {view.ble_key if view.ble_installed else '-'}",
            f"WiFi: {view.wifi_ssid or 'not connected'}",
            f"IP:   {view.wifi_ip or '-'}",
        ]
        y = 84
        for line in details:
            draw.text((TEXT_X, y), self._fit(draw, line, self.small_font, text_width),
                      fill=(214, 225, 236), font=self.small_font)
            y += 14

        items = [
            (ble_menu_label(view), ""),
            ("Scan networks", "kbd" if view.keyboard_ready else "no kbd"),
            ("Back to desktop", ""),
        ]
        y = 150
        for index, (label, meta) in enumerate(items):
            self._row(draw, y, label, meta, index == view.menu_index)
            y += 28

    def _scan(self, draw, view: View):
        width = self.width
        rows = scan_rows(view)
        lead_count = len(SCAN_LEADING)
        last_network = lead_count + len(view.networks) - 1

        draw.text((TEXT_X, 50), "All networks" if view.scan_wide else "Nearby",
                  fill=(130, 180, 230), font=self.small_font)
        counter = f"{view.scan_index + 1}/{len(rows)}"
        draw.text((width - TEXT_X - draw.textlength(counter, font=self.small_font), 50),
                  counter, fill=(100, 116, 134), font=self.small_font)

        y = 66
        for offset in range(VISIBLE_SCAN_ROWS):
            index = view.scan_window + offset
            if index >= len(rows):
                break
            label, meta = rows[index]
            is_action = index < lead_count or index > last_network
            self._row(draw, y, label, meta, index == view.scan_index, dim=is_action,
                      meta_color=(120, 230, 150) if meta == "now" else None)
            y += 28
            # Rules fence the network list off from the fixed rows.
            if view.networks and index in (lead_count - 1, last_network):
                draw.line((MARGIN, y + 2, width - MARGIN, y + 2), fill=(48, 62, 80), width=1)
                y += 4

    def _connecting(self, draw, view: View):
        width = self.width
        text_width = width - 2 * TEXT_X
        draw.text((TEXT_X, 62), "Connecting", fill=(120, 200, 255), font=self.title_font)
        draw.text((TEXT_X, 94), self._fit(draw, view.connect_ssid or "network", self.body_font, text_width),
                  fill=(255, 255, 255), font=self.body_font)

        track_top, track_height = 130, 10
        span = width - 2 * MARGIN
        draw.rounded_rectangle((MARGIN, track_top, width - MARGIN, track_top + track_height),
                               radius=5, fill=(24, 34, 48))
        segment = span // 3
        travel = (view.phase / 24.0) * (span + segment) - segment
        left = MARGIN + max(0, travel)
        right = MARGIN + min(span, travel + segment)
        if right > left:
            draw.rounded_rectangle((left, track_top, right, track_top + track_height),
                                   radius=5, fill=(60, 190, 100))

        draw.text((TEXT_X, 156), f"{view.elapsed}s elapsed", fill=(150, 166, 184), font=self.small_font)
        y = 178
        for line in ("Asking NetworkManager to join", "this network. This can take a", "few seconds."):
            draw.text((TEXT_X, y), line, fill=(118, 136, 156), font=self.small_font)
            y += 16

    def _result(self, draw, view: View):
        width = self.width
        text_width = width - 2 * TEXT_X
        ok = view.result_ok
        draw.rounded_rectangle((MARGIN, 56, width - MARGIN, 122), radius=12,
                               fill=(18, 40, 28) if ok else (44, 22, 26))
        draw.text((TEXT_X, 64), "Connected" if ok else "Not connected",
                  fill=(120, 230, 150) if ok else (240, 120, 120), font=self.title_font)
        draw.text((TEXT_X, 96), self._fit(draw, view.connect_ssid or "", self.body_font, text_width),
                  fill=(214, 225, 236), font=self.body_font)

        y = 136
        for line in self._wrap(draw, view.result_message, self.small_font, text_width, 5):
            draw.text((TEXT_X, y), line, fill=(150, 166, 184), font=self.small_font)
            y += 16

    def _entry(self, draw, view: View):
        width = self.width
        entering_ssid = view.mode == MODE_SSID
        text_width = width - 2 * TEXT_X

        draw.text((TEXT_X, 52), "Hidden network" if entering_ssid else "Password",
                  fill=(130, 180, 230), font=self.small_font)
        chip = "kbd" if view.keyboard_ready else "no kbd"
        draw.text((width - TEXT_X - draw.textlength(chip, font=self.small_font), 52), chip,
                  fill=(120, 230, 150) if view.keyboard_ready else (255, 180, 90),
                  font=self.small_font)

        draw.rounded_rectangle((MARGIN, 70, width - MARGIN, 118), radius=12, fill=(18, 28, 42))
        draw.text((TEXT_X, 76), "SSID" if entering_ssid else "Password",
                  fill=(255, 255, 255), font=self.small_font)
        if entering_ssid:
            typed = bool(view.ssid)
            value = view.ssid[-22:] if typed else "<empty>"
        else:
            typed = view.password_len > 0
            value = "*" * min(view.password_len, 22) if typed else (
                "<empty>" if view.password_known_secured else "<open network>"
            )
        draw.text((TEXT_X, 92), value, fill=(120, 255, 140) if typed else (110, 124, 140),
                  font=self.body_font)

        if not entering_ssid and view.ssid:
            draw.text((TEXT_X, 124), self._fit(draw, f"for {view.ssid}", self.small_font, text_width),
                      fill=(150, 166, 184), font=self.small_font)

        draw.line((MARGIN, 144, width - MARGIN, 144), fill=(48, 62, 80), width=1)
        guide = [
            ("Enter", "next" if entering_ssid else "connect"),
            ("Esc", "back"),
            ("Backspace", "delete"),
            ("Hold button", "cancel"),
        ]
        y = 154
        for name, meaning in guide:
            draw.text((TEXT_X, y), name, fill=(214, 225, 236), font=self.small_font)
            draw.text((TEXT_X + 92, y), meaning, fill=(118, 136, 156), font=self.small_font)
            y += 18

    def _footer(self, draw, view: View):
        """The quad-click exit gesture stays live in the daemon; it just does
        not take up a line on screen."""
        width, height = self.width, self.height
        top = height - 36
        draw.line((MARGIN, top, width - MARGIN, top), fill=(48, 62, 80), width=1)
        footer = view.status
        if not footer:
            if view.mode == MODE_CONNECTING:
                footer = "Please wait"
            elif view.mode == MODE_RESULT:
                footer = "Press to continue"
            elif view.busy:
                footer = "working..."
            elif view.mode in (MODE_MENU, MODE_SCAN):
                footer = "Press: next   Hold: select"
            else:
                footer = "Type on the USB keyboard"
        draw.text((TEXT_X, top + 10), self._fit(draw, footer, self.small_font, width - 2 * TEXT_X),
                  fill=(156, 214, 255), font=self.small_font)
