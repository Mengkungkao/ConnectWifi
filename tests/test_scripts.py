"""The installers can only really run on a board, so check here what can be
checked anywhere: that they parse, and that shared pieces stay valid."""

import glob
import json
import os
import shutil
import subprocess

import pytest

from conftest import ROOT

SCRIPTS = sorted(glob.glob(os.path.join(ROOT, "*.sh")) + glob.glob(os.path.join(ROOT, "setup", "*.sh")))


@pytest.mark.parametrize("path", SCRIPTS, ids=[os.path.relpath(p, ROOT) for p in SCRIPTS])
def test_script_parses(path):
    result = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    result = subprocess.run(["shellcheck", "-S", "warning", "-x", *SCRIPTS],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout


def test_ble_info_panel_works_on_every_board():
    with open(os.path.join(ROOT, "setup", "ble-info.json")) as fp:
        config = json.load(fp)
    commands = " ".join(item["command"] for item in config["info"])
    assert "vcgencmd" not in commands          # a Raspberry Pi-only tool
    assert {item["label"] for item in config["commands"]} == {"shutdown", "reboot"}


def _run_helper(snippet):
    """Source setup/common.sh as an installer would, then run snippet."""
    script = f'STEPS=1; . "{ROOT}/setup/common.sh" --check; {snippet}'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={**os.environ, "TERM": "dumb"})


@pytest.mark.skipif(os.geteuid() == 0, reason="the installer refuses to run as root")
def test_random_key_is_8_unambiguous_characters():
    result = _run_helper("random_key")
    assert len(result.stdout) == 8
    assert not set(result.stdout) & set("01ilo")


@pytest.mark.skipif(os.geteuid() == 0, reason="the installer refuses to run as root")
def test_bad_ble_names_are_refused_before_anything_runs():
    script = f'STEPS=1; . "{ROOT}/setup/common.sh" --check --ble-key "a b;reboot"'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 1 and "1-32" in result.stderr


# Real `sudo -l` listings. The Pi already has a rule for the BLE switch; the
# Orange Pi has NOPASSWD rules, just not that one.
PI_SUDO_L = """User jarvis may run the following commands on raspberrypi:
    (ALL : ALL) ALL
    (root) NOPASSWD: /sbin/shutdown, /sbin/reboot, /sbin/poweroff
    (root) NOPASSWD: /usr/bin/systemctl start sugar-wifi-config.service, /usr/bin/systemctl stop sugar-wifi-config.service
"""
OPI_SUDO_L = """User orangepi may run the following commands on orangepizero2w:
    (ALL : ALL) ALL
    (root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot
"""


def _with_fake_sudo(tmp_path, listing, exit_code=0):
    """A sudo on PATH that prints listing, and records its arguments."""
    fake = tmp_path / "sudo"
    fake.write_text(f"#!/bin/sh\necho \"$*\" >> {tmp_path}/sudo.args\n"
                    f"cat <<'EOF'\n{listing}EOF\nexit {exit_code}\n")
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", f'STEPS=1; . "{ROOT}/setup/common.sh" --check; ble_switch_passwordless'],
        capture_output=True, text=True, env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"})
    return result.returncode, (tmp_path / "sudo.args").read_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="the installer refuses to run as root")
@pytest.mark.parametrize("listing, exit_code, expected", [
    (PI_SUDO_L, 0, 0),
    (OPI_SUDO_L, 0, 1),
    ("User pi may run the following commands on raspberrypi:\n    (ALL) NOPASSWD: ALL\n", 0, 0),
    ("sudo: a password is required\n", 1, 1),
], ids=["pi-has-rule", "orangepi-lacks-rule", "nopasswd-all", "no-nopasswd-rules"])
def test_ble_switch_passwordless(tmp_path, listing, exit_code, expected):
    # The installer once asked `sudo -l COMMAND`, which only says whether the
    # command is allowed at all -- with a password everything is -- and so
    # skipped writing the rule on the Orange Pi.
    code, args = _with_fake_sudo(tmp_path, listing, exit_code)
    assert code == expected
    assert args.split() == ["-k", "-n", "-l"]      # never trusts a cached ticket


@pytest.mark.skipif(os.geteuid() == 0, reason="the installer refuses to run as root")
def test_fetch_falls_back_to_a_release_this_glibc_can_run(tmp_path):
    # Stand-ins for the downloads: "latest" dies in the loader the way
    # v2.3.0 does on Ubuntu 22.04; v2.2.3 runs.
    script = f"""
        STEPS=1; . "{ROOT}/setup/common.sh" --check
        download() {{
            case "$2" in
                */latest/*) printf '#!/bin/sh\\n# GLIBC_2.39\\necho "GLIBC_2.39 not found" >&2; exit 1\\n' > "$1" ;;
                *v2.2.3*)   printf '#!/bin/sh\\n# GLIBC_2.30\\nexit 0\\n' > "$1" ;;
            esac
        }}
        is_elf() {{ true; }}
        fetch_ble_binary "{tmp_path}/bin" aarch64 && echo "chose $BLE_RELEASE"
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "chose v2.2.3" in result.stdout
    assert "latest needs GLIBC 2.39" in result.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="the installer refuses to run as root")
def test_fetch_fails_when_no_release_runs(tmp_path):
    script = f"""
        STEPS=1; . "{ROOT}/setup/common.sh" --check
        download() {{ printf '#!/bin/sh\\nexit 1\\n' > "$1"; }}
        is_elf() {{ true; }}
        fetch_ble_binary "{tmp_path}/bin" aarch64
    """
    assert subprocess.run(["bash", "-c", script], capture_output=True, text=True).returncode == 1


def test_release_urls_try_the_mirror_then_github():
    result = _run_helper("ble_release_urls v2.2.3 aarch64; ble_release_urls latest armv7")
    assert result.stdout.split() == [
        "https://repo.pisugar.uk/PiSugar/sugar-wifi-conf/releases/download/v2.2.3/sugar-wifi-conf-aarch64",
        "https://github.com/PiSugar/sugar-wifi-conf/releases/download/v2.2.3/sugar-wifi-conf-aarch64",
        "https://repo.pisugar.uk/PiSugar/sugar-wifi-conf/releases/latest/download/sugar-wifi-conf-armv7",
        "https://github.com/PiSugar/sugar-wifi-conf/releases/latest/download/sugar-wifi-conf-armv7",
    ]
