"""Running nmcli and systemctl, and deciding when to use root for it.

The app is a child of whisplay-daemon, which runs as a normal user with
no login session. That matters twice over:

- polkit has no session to ask about, so it answers from the "any"
  column of each action. The installer adds a grant for the netdev group
  so NetworkManager's scan and connect work there.
- NetworkManager does not fail loudly when it lacks authorisation: an
  unprivileged `wifi list --rescan yes` exits 0 and returns the stale
  cache. There is no error to retry on, so privileges are probed up front.

A full passwordless sudo is used first when there is one. The installer
does not grant that; it grants only the two systemctl commands in
connectwifi.ble.
"""

from __future__ import annotations

import subprocess
from typing import Callable

Runner = Callable[[list, float], subprocess.CompletedProcess]


def run_subprocess(args: list, timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run, but a missing program or a timeout comes back as a
    failed result instead of an exception, so callers handle one shape."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: not installed")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"{args[0]} timed out")


def output_of(result: subprocess.CompletedProcess) -> str:
    return (result.stdout or "") if result.returncode == 0 else ""


def refused_by_sudo(result: subprocess.CompletedProcess) -> bool:
    """sudo itself said no (no rule, or it wants a password), as opposed to
    the command running as root and failing."""
    if result.returncode == 0:
        return False
    return "sudo:" in f"{result.stdout} {result.stderr}".lower()


class Commands:
    def __init__(self, runner: Runner = run_subprocess):
        self._runner = runner
        self.can_sudo = False

    def run(self, args: list, timeout: float = 10.0) -> subprocess.CompletedProcess:
        return self._runner(list(args), timeout)

    def probe_sudo(self) -> bool:
        self.can_sudo = self.run(["sudo", "-n", "true"], timeout=5).returncode == 0
        return self.can_sudo

    def run_privileged(self, args: list, timeout: float) -> subprocess.CompletedProcess:
        """Root first when a passwordless sudo exists, then as ourselves."""
        if self.can_sudo:
            result = self.run(["sudo", "-n", *args], timeout=timeout)
            if not refused_by_sudo(result):
                return result
            # sudoers refused this specific command; try as ourselves.
        return self.run(args, timeout=timeout)

    def short_error(self, text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ("not authorized", "authentication", "password is required")):
            return "Not allowed - run the installer again"
        return text.replace("Error: ", "").strip() or "Failed"
