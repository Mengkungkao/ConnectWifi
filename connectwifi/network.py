"""NetworkManager, through nmcli.

Everything here reads nmcli's terse (-t) output, which separates fields
with ':' and escapes a literal ':' or '\\' inside a field with a backslash.
The functions only parse and build argument lists; running them is the
job of connectwifi.system, so all of this can be tested off the board.
"""

from __future__ import annotations

from dataclasses import dataclass

OPEN_SECURITY = {"", "--", "none", "open"}
SCAN_FIELDS = "IN-USE,SSID,SIGNAL,SECURITY"


@dataclass
class Network:
    ssid: str
    signal: int
    security: str
    active: bool = False
    # NetworkManager has a profile for it: joining needs no password.
    saved: bool = False

    @property
    def is_open(self) -> bool:
        return self.security.strip().lower() in OPEN_SECURITY


@dataclass
class SavedNetwork:
    """A Wi-Fi profile NetworkManager keeps, password and all. Its name is
    often, but not always, the SSID: Raspberry Pi OS calls the one made at
    imaging "preconfigured"."""

    ssid: str
    uuid: str
    name: str = ""
    last_used: int = 0


def split_fields(line: str) -> list[str]:
    """nmcli -t escapes a literal colon inside a field as \\:"""
    fields, current, escaped = [], "", False
    for char in line:
        if escaped:
            current += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append(current)
            current = ""
        else:
            current += char
    fields.append(current)
    return fields


def parse_networks(output: str) -> list[Network]:
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`, one row
    per SSID, the network in use first and the rest strongest first."""
    strongest: dict[str, Network] = {}
    for line in output.splitlines():
        fields = split_fields(line)
        if len(fields) < 4:
            continue
        in_use, ssid, signal, security = fields[0], fields[1], fields[2], fields[3]
        if not ssid:
            continue
        try:
            strength = max(0, min(100, int(signal)))
        except ValueError:
            strength = 0
        active = in_use.strip() == "*"
        known = strongest.get(ssid)
        if known is None:
            strongest[ssid] = Network(ssid, strength, security, active)
        else:
            # One SSID, several BSSIDs: take the best signal, but the in-use
            # flag belongs to the SSID even when a weaker radio carries it.
            known.active = known.active or active
            if strength > known.signal:
                known.signal = strength
                known.security = security
    return sorted(strongest.values(), key=lambda n: (not n.active, -n.signal, n.ssid.lower()))


def wifi_device(output: str) -> str:
    """The first Wi-Fi interface in `nmcli -t -f DEVICE,TYPE device`. Most
    boards call it wlan0, but not all, and p2p-dev-wlan0 is type wifi-p2p."""
    for line in output.splitlines():
        fields = split_fields(line)
        if len(fields) >= 2 and fields[1] == "wifi":
            return fields[0]
    return ""


def active_ssid(output: str) -> str:
    """From `nmcli -t -f ACTIVE,SSID device wifi list`: the SSID in use,
    "(hidden)" when the network does not broadcast one, "" when offline."""
    for line in output.splitlines():
        fields = split_fields(line)
        if len(fields) >= 2 and fields[0] == "yes":
            return fields[1] or "(hidden)"
    return ""


def ipv4_address(output: str) -> str:
    """From `nmcli -t -f IP4.ADDRESS device show DEV`: the first address,
    without its prefix length."""
    for line in output.splitlines():
        name, _, value = line.partition(":")
        if name.startswith("IP4.ADDRESS"):
            address = value.split("/", 1)[0].strip()
            if address:
                return address
    return ""


def ssid_listed(output: str, ssid: str) -> bool:
    """Whether `nmcli -t -f SSID device wifi list` output contains ssid."""
    return any(split_fields(line)[0] == ssid for line in output.splitlines())


def scan_permitted(output: str) -> bool:
    """From `nmcli -t -f permission,value general permissions`. Without
    this, NetworkManager still answers a rescan request -- from its cache,
    without saying so -- so the app has to ask up front."""
    for line in output.splitlines():
        fields = split_fields(line)
        if len(fields) >= 2 and fields[0].endswith("wifi.scan"):
            return fields[1].strip() == "yes"
    return False


PROFILE_FIELDS = "connection.id,connection.uuid,connection.timestamp,802-11-wireless.ssid"


def wifi_profile_uuids(output: str) -> list[str]:
    """Wi-Fi profiles in `nmcli -t -f UUID,TYPE connection show`."""
    uuids = []
    for line in output.splitlines():
        fields = split_fields(line)
        if len(fields) >= 2 and fields[1] == "802-11-wireless":
            uuids.append(fields[0])
    return uuids


def parse_profiles(output: str) -> dict[str, SavedNetwork]:
    """`nmcli -t -f PROFILE_FIELDS connection show UUID...`: one block of
    `field:value` lines per profile, blank-line separated. Values are not
    escaped here, unlike in list output, so split at the first colon. When
    one SSID has several profiles -- retries leave "Home 1" behind -- the
    one used last wins."""
    saved: dict[str, SavedNetwork] = {}
    for block in output.split("\n\n"):
        values = {}
        for line in block.splitlines():
            name, _, value = line.partition(":")
            values[name] = value
        ssid, uuid = values.get("802-11-wireless.ssid", ""), values.get("connection.uuid", "")
        if not ssid or not uuid:
            continue
        try:
            last_used = int(values.get("connection.timestamp") or 0)
        except ValueError:
            last_used = 0
        known = saved.get(ssid)
        if known is None or last_used > known.last_used:
            saved[ssid] = SavedNetwork(ssid, uuid, values.get("connection.id", ""), last_used)
    return saved


def profiles_command(uuids: list[str]) -> list[str]:
    return ["nmcli", "-t", "-f", PROFILE_FIELDS, "connection", "show", *uuids]


def up_command(uuid: str, device: str) -> list[str]:
    return ["nmcli", "connection", "up", "uuid", uuid, *_ifname(device)]


def set_password_command(uuid: str, password: str) -> list[str]:
    return ["nmcli", "connection", "modify", "uuid", uuid, "802-11-wireless-security.psk", password]


def delete_command(uuid: str) -> list[str]:
    return ["nmcli", "connection", "delete", "uuid", uuid]


# How NetworkManager words a rejected or missing password. A wrong WPA key
# ends with it asking for new secrets and, with no one to answer, "(7)
# Secrets were required, but not provided". Some drivers never report the
# failed handshake, so it shows up as the supplicant timing out instead.
AUTH_FAILURES = (
    "secrets were required",
    "802.1x supplicant",
    "802-11-wireless-security.psk",
    "no secrets",
)


def is_auth_failure(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in AUTH_FAILURES)


def _ifname(device: str) -> list[str]:
    return ["ifname", device] if device else []


def list_command(device: str, rescan: str, fields: str = SCAN_FIELDS) -> list[str]:
    """rescan is "yes" (sweep now) or "no" (the cache only). nmcli's own
    default, "auto", sweeps whenever the cache is older than 30 seconds,
    so anything run on a timer must pass "no" or it keeps the radio busy."""
    return ["nmcli", "-t", "-f", fields, "device", "wifi", "list",
            *_ifname(device), "--rescan", rescan]


def connect_command(ssid: str, password: str, device: str, hidden: bool) -> list[str]:
    args = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    args += _ifname(device)
    if hidden:
        args += ["hidden", "yes"]
    return args
