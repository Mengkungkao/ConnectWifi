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
def test_hide_takes_the_wifi_entries_off_the_desktop_only(tmp_path, version):
    root, home = make_whisplay(tmp_path, version)
    before = daemon_view(root)
    assert "whisplay-wifi" in before["apps"]
    original = (root / "daemon/internal_apps/manager.py").read_text()

    result = tool("hide", root, home)
    assert result.returncode == 0, result.stderr
    after = daemon_view(root)
    assert after["apps"] == [app for app in before["apps"] if app != "whisplay-wifi"]
    # The app is still wired in exactly as before: only its listing went.
    assert after["wifi_is_internal"] is True and after["typing"] is True
    assert (root / "daemon/internal_apps/wifi_app.py").exists()
    edited = (root / "daemon/internal_apps/manager.py").read_text()
    removed = [line for line in original.splitlines() if line not in edited.splitlines()]
    assert all("self.wifi.builtin_app()" in line for line in removed)
    # The example's menu entries go; its program would stay.
    assert not (root / "daemon/default_apps/whisplay-wifi-config.json").exists()
    assert not (home / ".whisplay-daemon/app/whisplay-wifi-config.json").exists()
    assert tool("status", root, home).stdout.strip() == \
        "built-in WiFi: hidden; example WiFi Config: hidden"


@pytest.mark.parametrize("version", VERSIONS)
def test_restore_puts_back_exactly_what_was_there(tmp_path, version):
    root, home = make_whisplay(tmp_path, version)
    original = snapshot(root, home)
    assert tool("hide", root, home).returncode == 0
    result = tool("restore", root, home)
    assert result.returncode == 0, result.stderr
    assert snapshot(root, home) == original
    assert "whisplay-wifi" in daemon_view(root)["apps"]


def test_hide_twice_is_harmless(tmp_path):
    root, home = make_whisplay(tmp_path, VERSIONS[0])
    tool("hide", root, home)
    again = tool("hide", root, home)
    assert again.returncode == 0 and again.stdout.strip() == "nothing to do"
    # The backup still holds the true original, not the edited file.
    backup = root / "daemon/internal_apps/.connectwifi-backup/manager.py"
    assert backup.read_text() == (FIXTURES / VERSIONS[0]).read_text()


def test_an_unfamiliar_desktop_list_is_left_untouched(tmp_path):
    # A future version that builds the list some other way: the Wi-Fi entry
    # cannot be taken out as one item, so nothing is changed.
    root, home = make_whisplay(tmp_path, VERSIONS[0], example=False)
    manager = root / "daemon/internal_apps/manager.py"
    source = manager.read_text()
    source = source.replace("            self.wifi.builtin_app(),\n",
                            "            self.wifi.builtin_app() if self.wifi else None,\n")
    manager.write_text(source)
    assert tool("status", root, home).stdout.strip().startswith("built-in WiFi: listed")
    result = tool("hide", root, home)
    assert result.returncode == 1 and "does not know" in result.stderr
    assert manager.read_text() == source
    assert tool("status", root, home).stdout.strip().startswith("built-in WiFi: listed")


def test_an_edit_that_strays_is_undone(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.join(ROOT, "setup"))
    import whisplay_wifi

    root, home = make_whisplay(tmp_path, VERSIONS[0], example=False)
    manager = root / "daemon/internal_apps/manager.py"
    original = manager.read_text()
    real_unlist = whisplay_wifi.unlist
    monkeypatch.setattr(whisplay_wifi, "unlist",
                        lambda source: real_unlist(source).replace("self._lock", "self._lock2", 1))
    with pytest.raises(whisplay_wifi.Refused, match="beyond the desktop list"):
        whisplay_wifi.hide(root)
    assert manager.read_text() == original


def test_not_a_whisplay_checkout(tmp_path):
    result = tool("status", tmp_path, tmp_path)
    assert result.returncode == 1 and "not found" in result.stderr
