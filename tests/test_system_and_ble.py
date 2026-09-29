from conftest import FakeRunner, done

from connectwifi import ble
from connectwifi.system import Commands, refused_by_sudo, run_subprocess

# Real `systemctl show` output from the Raspberry Pi.
PI_SHOW = (
    "ExecStart={ path=/opt/sugar-wifi-config/sugar-wifi-conf ; argv[]=/opt/sugar-wifi-config/"
    "sugar-wifi-conf --name raspberrypi --key pisugar --config /opt/sugar-wifi-config/"
    "custom_config.json ; ignore_errors=no ; start_time=[Mon 2026-09-28 15:50:33 BST] ; "
    "stop_time=[n/a] ; pid=862 ; code=(null) ; status=0/0 }\n"
    "LoadState=loaded\nActiveState=active\n"
)
NOT_FOUND_SHOW = "LoadState=not-found\nActiveState=inactive\nExecStart=\n"
SUDO_REFUSED = done(returncode=1, stderr="sudo: a password is required\n")


def is_sudo(args):
    return args[0] == "sudo"


def test_missing_program_is_a_result_not_an_exception():
    result = run_subprocess(["connectwifi-no-such-program"], timeout=5)
    assert result.returncode == 127


def test_refused_by_sudo_tells_sudo_apart_from_the_command():
    assert refused_by_sudo(SUDO_REFUSED)
    assert not refused_by_sudo(done(returncode=5, stderr="Failed to stop unit"))
    assert not refused_by_sudo(done())


def test_run_privileged_uses_sudo_only_when_there_is_a_full_one():
    runner = FakeRunner()
    commands = Commands(runner)
    commands.run_privileged(["nmcli", "x"], timeout=5)
    assert runner.calls == [["nmcli", "x"]]

    commands.can_sudo = True
    commands.run_privileged(["nmcli", "x"], timeout=5)
    assert runner.calls[-1] == ["sudo", "-n", "nmcli", "x"]


def test_run_privileged_falls_back_when_sudoers_refuses_the_command():
    runner = FakeRunner((is_sudo, SUDO_REFUSED))
    commands = Commands(runner)
    commands.can_sudo = True
    commands.run_privileged(["nmcli", "x"], timeout=5)
    assert runner.calls == [["sudo", "-n", "nmcli", "x"], ["nmcli", "x"]]


def test_short_error():
    commands = Commands(FakeRunner())
    assert commands.short_error("Error: Not authorized to control networking.") == \
        "Not allowed - run the installer again"
    assert commands.short_error("Error: Connection activation failed: Secrets were required") == \
        "Connection activation failed: Secrets were required"


def test_parse_show_reads_name_key_and_state():
    status = ble.parse_show(PI_SHOW)
    assert status == ble.BleStatus(installed=True, active=True, name="raspberrypi", key="pisugar")


def test_parse_show_for_a_missing_service():
    status = ble.parse_show(NOT_FOUND_SHOW)
    assert not status.installed and not status.active


def test_parse_show_defaults_when_the_unit_passes_no_name_or_key():
    status = ble.parse_show("LoadState=loaded\nActiveState=inactive\n"
                            "ExecStart={ path=/x ; argv[]=/x --config c.json ; }\n")
    assert (status.name, status.key) == (ble.DEFAULT_NAME, ble.DEFAULT_KEY)


def test_set_running_uses_the_narrow_sudo_rule_directly():
    # The installer's sudoers rule allows exactly this command. The original
    # app probed `sudo -n true` instead, which that rule refuses, and then
    # ran plain systemctl, which polkit refuses outside a login session.
    runner = FakeRunner()
    result = ble.BleService(Commands(runner)).set_running(True)
    assert result.returncode == 0
    assert runner.calls == [["sudo", "-n", "systemctl", "start", ble.SERVICE]]


def test_set_running_falls_back_to_plain_systemctl_without_the_rule():
    runner = FakeRunner((is_sudo, SUDO_REFUSED))
    ble.BleService(Commands(runner)).set_running(False)
    assert runner.calls[-1] == ["systemctl", "stop", ble.SERVICE]


def test_set_running_reports_a_real_failure_without_retrying():
    failed = done(returncode=5, stderr="Job for sugar-wifi-config.service failed.")
    runner = FakeRunner((is_sudo, failed))
    result = ble.BleService(Commands(runner)).set_running(True)
    assert result is failed and len(runner.calls) == 1
