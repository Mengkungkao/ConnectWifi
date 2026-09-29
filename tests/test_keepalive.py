import os
import subprocess
import sys

from conftest import ROOT, FakeRunner, done

from connectwifi import keepalive, network
from connectwifi.system import Commands

DEVICES = done("lo:loopback\np2p-dev-wlan0:wifi-p2p\nwlan0:wifi\n")
PROFILES = ("connection.id:diswifi5G\nconnection.uuid:u-home\nconnection.timestamp:1790660724\n"
            "802-11-wireless.ssid:diswifi5G\n\n"
            "connection.id:Office\nconnection.uuid:u-office\nconnection.timestamp:1790000000\n"
            "802-11-wireless.ssid:Office\n\n"
            "connection.id:Travel\nconnection.uuid:u-travel\nconnection.timestamp:1790999999\n"
            "802-11-wireless.ssid:Travel\n")
IN_RANGE = " :diswifi5G:49:WPA2\n :diswifi5G:22:WPA2\n :Office:40:WPA2\n :Neighbour:60:WPA2\n"


def status(state, autoconnect="yes"):
    return done(f"GENERAL.STATE:{state}\nGENERAL.AUTOCONNECT:{autoconnect}\n")


def fake(state="30 (disconnected)", autoconnect="yes", in_range=IN_RANGE, up=None):
    return FakeRunner(
        (lambda a: "DEVICE,TYPE" in a, DEVICES),
        (lambda a: "GENERAL.STATE,GENERAL.AUTOCONNECT" in a, status(state, autoconnect)),
        (lambda a: "UUID,TYPE" in a, done("u-home:802-11-wireless\nu-office:802-11-wireless\n"
                                          "u-travel:802-11-wireless\nlo:loopback\n")),
        (lambda a: network.PROFILE_FIELDS in a, done(PROFILES)),
        (lambda a: "list" in a, done(in_range)),
        (lambda a: a[1:3] == ["connection", "up"], up or done("Connection successfully activated.\n")),
    )


def check(runner):
    return keepalive.check(Commands(runner), settle=0, sleep=lambda seconds: None)


def test_a_healthy_connection_is_left_alone_quietly():
    runner = fake(state="100 (connected)")
    assert check(runner) is None
    assert not runner.calls_with("list") and not runner.calls_with("up")


def test_connecting_or_switched_off_is_not_its_business():
    for state in ("50 (connecting (configuring))", "60 (connecting (need authentication))",
                  "20 (unavailable)", "10 (unmanaged)"):
        runner = fake(state=state)
        assert check(runner) is None, state
        assert not runner.calls_with("up")


def test_a_deliberate_disconnect_is_respected():
    runner = fake(autoconnect="no")
    assert "on purpose" in check(runner)
    assert not runner.calls_with("up")


def test_networkmanager_gets_a_moment_to_reconnect_by_itself():
    runner = fake()
    looks = iter([status("30 (disconnected)"), status("70 (connecting (getting IP configuration))")])
    runner.add(lambda a: "GENERAL.STATE,GENERAL.AUTOCONNECT" in a, lambda a: next(looks))
    waited = []
    assert keepalive.check(Commands(runner), settle=15, sleep=waited.append) is None
    assert waited == [15] and not runner.calls_with("up")


def test_it_brings_back_the_most_recent_saved_network_in_range():
    # Travel was used last but is not in range; diswifi5G is the latest
    # one that is. Its two access points are one network.
    runner = fake()
    assert check(runner) == "wlan0 was disconnected; reconnected to diswifi5G"
    assert runner.calls_with("up") == [["nmcli", "connection", "up", "uuid", "u-home", "ifname", "wlan0"]]
    scan = runner.calls_with("list")[0]
    assert scan[-2:] == ["--rescan", "yes"]         # a fresh look: the cache predates the drop


def test_nothing_saved_in_range():
    runner = fake(in_range=" :Neighbour:60:WPA2\n")
    assert check(runner) == "wlan0 is disconnected and no saved network is in range"
    assert not runner.calls_with("up")


def test_a_failed_reconnect_says_why():
    runner = fake(up=done(returncode=4, stderr="Error: Connection activation failed: (53) The Wi-Fi "
                                               "network could not be found.\n"))
    assert check(runner).startswith("wlan0 is disconnected; diswifi5G did not come back: Connection")


class Stop(Exception):
    pass


def run_minutes(runner, minutes):
    """The service loop, for a number of minutes, with no real waiting."""
    logged, slept = [], []

    def sleep(seconds):
        slept.append(seconds)
        if slept.count(keepalive.INTERVAL_SEC) >= minutes:
            raise Stop

    try:
        keepalive.run(Commands(runner), sleep=sleep, log=logged.append)
    except Stop:
        pass
    return logged, slept


def test_the_service_waits_after_boot_then_looks_every_minute():
    logged, slept = run_minutes(fake(state="100 (connected)"), 3)
    assert slept[0] == keepalive.FIRST_LOOK_SEC
    assert slept[1:] == [keepalive.INTERVAL_SEC] * 3
    assert logged == []                               # healthy minutes are silent


def test_the_same_news_is_logged_once():
    logged, _ = run_minutes(fake(in_range=" :Neighbour:60:WPA2\n"), 5)
    assert logged == ["connectwifi-keepalive: wlan0 is disconnected and no saved network is in range"]


def test_a_bad_minute_does_not_end_the_service():
    runner = fake()
    runner.add(lambda a: "DEVICE,TYPE" in a, lambda a: 1 / 0)
    logged, slept = run_minutes(runner, 3)
    assert len(slept) == 4 and logged[0].startswith("connectwifi-keepalive: check failed")


def test_wifi_powersave_reads_the_merged_config(tmp_path):
    # The Orange Pi's real `NetworkManager --print-config`, trimmed.
    fake_nm = tmp_path / "NetworkManager"
    fake_nm.write_text("#!/bin/sh\ncat <<'EOF'\n"
                       "# NetworkManager configuration: /etc/NetworkManager/NetworkManager.conf "
                       "(etc: 20-override-wifi-powersave-disable.conf, default-wifi-powersave-on.conf)\n"
                       "[main]\ndns=default\n[connection]\nwifi.powersave=3\nEOF\n")
    fake_nm.chmod(0o755)
    script = f'STEPS=1; . "{ROOT}/setup/common.sh" --check; wifi_powersave'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"})
    assert result.stdout.strip() == "3"


def test_runs_as_a_module():
    result = subprocess.run([sys.executable, "-c", "import connectwifi.keepalive as k; print(k.DISCONNECTED)"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.stdout.strip() == "30"
