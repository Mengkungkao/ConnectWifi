"""Bring Wi-Fi back when NetworkManager has given up on it.

On the Orange Pi Zero 2W the router sometimes drops the board, and the
Wi-Fi driver's quick reconnect fails its handshake (the kernel logs a
cfg80211 warning as it does). wpa_supplicant reports that as a wrong
password; NetworkManager asks for a new one, a headless board has no one
to answer, so it marks the network failed and stops trying until someone
reconnects by hand. It once stayed off for two hours.

This runs as a small service (connectwifi-keepalive) and looks once a
minute. When Wi-Fi is disconnected -- not connecting, not switched off,
and not disconnected on purpose with `nmcli device disconnect` -- and still
is a little later, it brings up the saved network used most recently among
those in range, just as reconnecting by hand did. It runs as the app's
user, not root.

    python3 -m connectwifi.keepalive          # the service
    python3 -m connectwifi.keepalive --once   # one look, now
"""

from __future__ import annotations

import sys
import time

from connectwifi import network
from connectwifi.system import Commands, output_of

DISCONNECTED = "30"   # NM_DEVICE_STATE_DISCONNECTED
INTERVAL_SEC = 60
FIRST_LOOK_SEC = 120  # at boot NetworkManager connects by itself; let it
SETTLE_SEC = 15       # NetworkManager's own retries come within seconds


def device_status(commands: Commands, device: str) -> tuple[str, str]:
    """GENERAL.STATE ("30 (disconnected)") and GENERAL.AUTOCONNECT, which
    NetworkManager turns to "no" when someone disconnects the device."""
    values = {}
    output = output_of(commands.run(
        ["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.AUTOCONNECT", "device", "show", device], timeout=10))
    for line in output.splitlines():
        name, _, value = line.partition(":")
        values[name] = value.strip()
    return values.get("GENERAL.STATE", ""), values.get("GENERAL.AUTOCONNECT", "")


def _disconnected(state: str) -> bool:
    return state.split(" ")[0] == DISCONNECTED


def check(commands: Commands, settle: float = SETTLE_SEC, sleep=time.sleep) -> str | None:
    """One look, and one reconnect if it is needed. Returns what it did, or
    None when Wi-Fi needs nothing, so a healthy minute logs nothing."""
    device = network.wifi_device(output_of(
        commands.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], timeout=10)))
    if not device:
        return None
    state, autoconnect = device_status(commands, device)
    if not _disconnected(state):
        return None
    if autoconnect != "yes":
        return f"{device} was disconnected on purpose; leaving it"
    sleep(settle)
    state, autoconnect = device_status(commands, device)
    if not _disconnected(state) or autoconnect != "yes":
        return None   # NetworkManager, or a person, took it from here

    uuids = network.wifi_profile_uuids(output_of(
        commands.run(["nmcli", "-t", "-f", "UUID,TYPE", "connection", "show"], timeout=10)))
    saved = network.parse_profiles(output_of(
        commands.run(network.profiles_command(uuids), timeout=10))) if uuids else {}
    if not saved:
        return None
    in_range = {found.ssid for found in network.parse_networks(output_of(
        commands.run(network.list_command(device, "yes"), timeout=45)))}
    candidates = sorted((profile for profile in saved.values() if profile.ssid in in_range),
                        key=lambda profile: profile.last_used, reverse=True)
    if not candidates:
        return f"{device} is disconnected and no saved network is in range"

    best = candidates[0]
    result = commands.run(network.up_command(best.uuid, device), timeout=90)
    if result.returncode == 0:
        return f"{device} was disconnected; reconnected to {best.ssid}"
    reason = (result.stderr or result.stdout).strip().replace("Error: ", "")
    return f"{device} is disconnected; {best.ssid} did not come back: {reason}"


def run(commands: Commands, sleep=time.sleep, log=print, first: float = FIRST_LOOK_SEC,
        interval: float = INTERVAL_SEC):
    """Look every interval, forever. The same message twice in a row -- "no
    saved network in range" for an hour away from home -- is logged once."""
    sleep(first)
    last = None
    while True:
        try:
            message = check(commands, sleep=sleep)
        except Exception as exc:   # a bad minute must not end the service
            message = f"check failed: {exc}"
        if message and message != last:
            log(f"connectwifi-keepalive: {message}")
        last = message
        sleep(interval)


def main(argv: list[str]):
    commands = Commands()
    if "--once" in argv:
        message = check(commands, settle=0)
        print(f"connectwifi-keepalive: {message or 'Wi-Fi needs nothing'}", flush=True)
        return
    run(commands, log=lambda line: print(line, flush=True))


if __name__ == "__main__":
    main(sys.argv[1:])
