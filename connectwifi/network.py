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

    @property
    def is_open(self) -> bool:
        return self.security.strip().lower() in OPEN_SECURITY


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
