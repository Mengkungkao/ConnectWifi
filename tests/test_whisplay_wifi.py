"""setup/whisplay_wifi.py against the two Whisplay versions in use: the
current one (1066486, on the Orange Pi) and an older one (4f8a3ba, on
the Raspberry Pi), whose manager.py is laid out differently."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

TOOL = os.path.join(ROOT, "setup", "whisplay_wifi.py")
FIXTURES = Path(ROOT) / "tests" / "fixtures"
VERSIONS = ["manager_1066486.py", "manager_4f8a3ba.py"]

STUB = '''from types import SimpleNamespace
{const} = "{app_id}"
class {cls}:
    def __init__(self, *args):
        pass
    def builtin_app(self):
        return SimpleNamespace(app_id={const})
    def set_error(self, text):
        pass
    def text_input_active(self):
        return {typing}
    def start(self):
        pass
    def stop(self):
        pass
'''
APPS = [
    ("bluetooth_app", "BLUETOOTH_APP_ID", "whisplay-bluetooth", "BluetoothInternalApp", False),
    ("wifi_app", "WIFI_APP_ID", "whisplay-wifi", "WifiInternalApp", True),
    ("volume_app", "VOLUME_APP_ID", "whisplay-volume", "VolumeInternalApp", False),
    ("system_app", "SYSTEM_APP_ID", "whisplay-system", "SystemInternalApp", False),
]


def make_whisplay(tmp_path, version, example=True):
    """A Whisplay tree: the real manager.py, stand-ins for the apps."""
    root = tmp_path / "Whisplay"
    apps = root / "daemon" / "internal_apps"
    apps.mkdir(parents=True)
    (apps / "__init__.py").write_text("")
    (apps / "manager.py").write_text((FIXTURES / version).read_text())
    for module, const, app_id, cls, typing in APPS:
        (apps / f"{module}.py").write_text(STUB.format(const=const, app_id=app_id, cls=cls, typing=typing))
    home = tmp_path / "home"
    (home / ".whisplay-daemon" / "app").mkdir(parents=True)
    if example:
        (root / "daemon" / "default_apps").mkdir()
        seed = {"app_id": "whisplay-wifi-config", "cwd": "__EXAMPLE_DIR__"}
        (root / "daemon" / "default_apps" / "whisplay-wifi-config.json").write_text(json.dumps(seed))
        entry = {"app_id": "whisplay-wifi-config", "cwd": "/home/pi/Whisplay/example"}
        (home / ".whisplay-daemon" / "app" / "whisplay-wifi-config.json").write_text(json.dumps(entry))
    return root, home


def tool(action, root, home):
    return subprocess.run([sys.executable, TOOL, action, str(root)], capture_output=True, text=True,
                          env={**os.environ, "HOME": str(home)})


def daemon_view(root):
    """What the edited manager tells the daemon."""
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
from internal_apps.manager import InternalAppManager
try:
    manager = InternalAppManager()
except TypeError:
    manager = InternalAppManager(lambda: None)
manager._set_error("probe")
print(json.dumps({"apps": [a.app_id for a in manager.builtin_apps()],
                  "typing": manager.text_input_active(),
                  "wifi_is_internal": manager.is_internal_app("whisplay-wifi")}))
"""
    result = subprocess.run([sys.executable, "-c", script, str(root / "daemon")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def snapshot(root, home):
    files = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
             and "__pycache__" not in p.parts}
    files.update({p.relative_to(home): p.read_bytes() for p in home.rglob("*.json")})
    return files


@pytest.mark.parametrize("version", VERSIONS)
def test_remove_takes_the_wifi_app_off_the_daemon(tmp_path, version):
    root, home = make_whisplay(tmp_path, version)
    before = daemon_view(root)
    assert "whisplay-wifi" in before["apps"] and before["typing"] is True

    result = tool("remove", root, home)
    assert result.returncode == 0, result.stderr
    after = daemon_view(root)
    assert "whisplay-wifi" not in after["apps"]
    assert after["apps"] == [app for app in before["apps"] if app != "whisplay-wifi"]
    assert after["typing"] is False and after["wifi_is_internal"] is False
    assert not (root / "daemon/internal_apps/wifi_app.py").exists()
    assert not (root / "daemon/default_apps/whisplay-wifi-config.json").exists()
    assert not (home / ".whisplay-daemon/app/whisplay-wifi-config.json").exists()
    assert tool("status", root, home).stdout.strip() == \
        "built-in WiFi: removed; example WiFi Config: removed"


@pytest.mark.parametrize("version", VERSIONS)
def test_restore_puts_back_exactly_what_was_there(tmp_path, version):
    root, home = make_whisplay(tmp_path, version)
    original = snapshot(root, home)
    assert tool("remove", root, home).returncode == 0
    result = tool("restore", root, home)
    assert result.returncode == 0, result.stderr
    assert snapshot(root, home) == original
    assert "whisplay-wifi" in daemon_view(root)["apps"]


def test_remove_twice_is_harmless(tmp_path):
    root, home = make_whisplay(tmp_path, VERSIONS[0])
    tool("remove", root, home)
    again = tool("remove", root, home)
    assert again.returncode == 0 and again.stdout.strip() == "nothing to do"
    # The backup still holds the true original, not the edited file.
    backup = root / "daemon/internal_apps/.connectwifi-backup/manager.py"
    assert backup.read_text() == (FIXTURES / VERSIONS[0]).read_text()


def test_an_unfamiliar_whisplay_is_left_untouched(tmp_path):
    # A future version that uses the app somewhere this tool does not know.
    root, home = make_whisplay(tmp_path, VERSIONS[0], example=False)
    manager = root / "daemon/internal_apps/manager.py"
    manager.write_text(manager.read_text() + "\n\ndef poke(m):\n    return m.wifi.state if m else None\n")
    before = manager.read_bytes()
    result = tool("remove", root, home)
    assert result.returncode == 1 and "still refers" in result.stderr
    assert manager.read_bytes() == before
    assert (root / "daemon/internal_apps/wifi_app.py").exists()


def test_not_a_whisplay_checkout(tmp_path):
    result = tool("status", tmp_path, tmp_path)
    assert result.returncode == 1 and "not found" in result.stderr
