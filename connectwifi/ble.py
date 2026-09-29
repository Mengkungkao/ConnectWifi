"""PiSugar's sugar-wifi-conf: the Bluetooth LE side of Wi-Fi setup.

The service lets a phone send an SSID and password over Bluetooth, which
is how a board with no keyboard gets onto Wi-Fi. This app only shows its
advertised name and key, and starts or stops it.

Starting and stopping go through `sudo -n systemctl start|stop`, which
the installer's sudoers rule allows and nothing else. Probing for a full
sudo with `sudo -n true` would fail against that narrow rule, and plain
systemctl from a session-less daemon child is refused by polkit
("Interactive authentication required"), so the rule is tried directly.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from connectwifi.system import Commands, refused_by_sudo

SERVICE = "sugar-wifi-config.service"
# sugar-wifi-conf's own defaults, for a unit that does not pass them.
DEFAULT_NAME = "raspberrypi"
DEFAULT_KEY = "pisugar"


@dataclass
class BleStatus:
    installed: bool = False
    active: bool = False
    name: str = DEFAULT_NAME
    key: str = DEFAULT_KEY


def parse_show(output: str) -> BleStatus:
    """`systemctl show SERVICE -p LoadState -p ActiveState -p ExecStart`."""
    values = {}
    for line in output.splitlines():
        name, _, value = line.partition("=")
        values[name] = value
    exec_start = values.get("ExecStart", "")
    name = re.search(r"--name[= ]+(\S+)", exec_start)
    key = re.search(r"--key[= ]+(\S+)", exec_start)
    return BleStatus(
        installed=values.get("LoadState") == "loaded",
        active=values.get("ActiveState") == "active",
        name=name.group(1) if name else DEFAULT_NAME,
        key=key.group(1) if key else DEFAULT_KEY,
    )


class BleService:
    def __init__(self, commands: Commands):
        self.commands = commands

    def status(self) -> BleStatus:
        result = self.commands.run(
            ["systemctl", "show", SERVICE, "-p", "LoadState", "-p", "ActiveState", "-p", "ExecStart"],
            timeout=5,
        )
        if result.returncode != 0:
            return BleStatus()
        return parse_show(result.stdout)

    def set_running(self, run: bool) -> subprocess.CompletedProcess:
        action = "start" if run else "stop"
        result = self.commands.run(["sudo", "-n", "systemctl", action, SERVICE], timeout=15)
        if not refused_by_sudo(result):
            return result
        return self.commands.run(["systemctl", action, SERVICE], timeout=15)
