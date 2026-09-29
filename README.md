# Connect WiFi

Join a Wi-Fi network from the Whisplay HAT's screen on a Raspberry Pi or
Orange Pi, with the HAT's button and a USB or Bluetooth keyboard. For a
board with no keyboard, the app also switches PiSugar's Bluetooth LE
setup service on and off, so a phone can send the network name and
password instead.

It is a standalone, installable version of the *WiFi Config* app from
Whisplay's `example/wifi_config_app.py`, with the fixes listed under
[What changed](#what-changed-from-whisplays-wifi-config), and it replaces
Whisplay's own Wi-Fi apps, which the installer can remove.

![Menu, BLE not installed, nearby networks, all networks, hidden network, password, connecting, connected, failed](docs/screens.png)

## Requirements

- Raspberry Pi (Raspberry Pi OS Bookworm or later) or Orange Pi Zero 2W/3W
  (Orange Pi OS or Armbian), with a Whisplay HAT.
- `whisplay-daemon` installed and running
  ([PiSugar/Whisplay](https://github.com/PiSugar/Whisplay)). It owns the
  HAT's screen and button, so the app needs it, and nothing else from
  Whisplay: see [Standalone](#standalone).
- NetworkManager managing the Wi-Fi interface (the default on both OSes).
- Python 3.9+, Pillow, NumPy (the installer offers to apt-install them).

## Install

From a development machine, one command copies the project and runs the
installer on the board (it asks for the board's sudo password there):

```bash
./deploy.sh orangepi@192.168.0.130 --setup
./deploy.sh pi@raspberrypi.local --setup
```

Or on the board itself:

```bash
git clone <this repo> ~/ConnectWifi   # or copy the folder over
cd ~/ConnectWifi
./setup.sh              # picks install-raspberrypi.sh or install-orangepi.sh
```

Run it as your normal user, not with `sudo`; it asks for sudo only for the
steps that need it, and asks before each change. Options, for either board:

| Option | Effect |
|--------|--------|
| `--check` | report only; change nothing |
| `--yes` | accept every prompt |
| `--no-ble` | leave out the Bluetooth LE setup service |
| `--keep-whisplay-wifi` | keep Whisplay's own WiFi apps on the desktop |
| `--ble-name NAME --ble-key KEY` | what the phone sees and must send (default: the hostname and a random 8-character key) |
| `--country XX` | Raspberry Pi only: set the Wi-Fi country, without which Raspberry Pi OS keeps Wi-Fi blocked |

### What the installer does

1. Checks the board, and installs `python3-pil python3-numpy fonts-dejavu-core bluez curl rfkill` if missing.
2. Checks `whisplay-daemon` is running and answering on its socket.
3. Checks the radios. **Raspberry Pi:** `dtoverlay=disable-wifi/disable-bt` in `config.txt`, and the Wi-Fi country. **Orange Pi:** rfkill, the vendor Bluetooth service (`sprd-bluetooth`/`aw859a-bluetooth`), `bluetooth.service`.
4. Makes sure the app may scan and connect from under `whisplay-daemon`, outside any login session: your user in `netdev`, and, only if NetworkManager would refuse, a polkit grant for `netdev`. Polkit before 0.106 (Ubuntu 22.04 on the Orange Pi) gets a `.pkla` file, newer polkit a JavaScript rule.
5. Installs PiSugar's `sugar-wifi-conf` to `/opt/sugar-wifi-config` as `sugar-wifi-config.service`. It runs each download once with `--help` before installing it: the newest release, v2.3.0, is built against glibc 2.39 (Ubuntu 24.04, Debian 13) and cannot start on Ubuntu 22.04 (Orange Pi OS), so there it falls back to v2.2.3, which needs glibc 2.30. A rerun replaces an installed copy that cannot run. The unit gives up after 5 failed starts instead of retrying forever. An existing install keeps its name and key. The info panel the phone shows (`setup/ble-info.json`) reads the temperature from sysfs, so it works on the Orange Pi too; PiSugar's default calls `vcgencmd`, which exists only on the Pi.
6. Adds `/etc/sudoers.d/connectwifi`, which allows exactly `systemctl start|stop sugar-wifi-config.service` without a password and nothing else. This is what the app's *Turn BLE on/off* runs. It is skipped only when sudoers already has a `NOPASSWD` rule covering both commands (read with `sudo -k -n -l`, so a sudo ticket cached earlier in the run cannot fool it).
7. Registers *Connect WiFi* on the HAT desktop.
8. Offers to remove Whisplay's own Wi-Fi apps, which this one replaces (see below), then restarts `whisplay-daemon` so the change takes effect. That closes the app on screen, so it asks first.
9. Runs the test suite if pytest is installed.

### Removing Whisplay's own WiFi apps

Whisplay has two, and ConnectWifi needs neither:

- the daemon's built-in **WiFi** app (`whisplay-wifi`), which is wired into
  `daemon/internal_apps/manager.py` in code;
- the example **WiFi Config** (`whisplay-wifi-config`), which Whisplay's
  daemon installer seeds from `daemon/default_apps/`.

`setup/whisplay_wifi.py` unhooks the built-in app from `manager.py` and
deletes `wifi_app.py`, and deletes the example's desktop entry and its seed,
so re-running Whisplay's installer does not bring it back. It edits the
checkout the running daemon comes from (read from the service's
`ExecStart`). `manager.py` differs between Whisplay versions, so it edits
the code's structure rather than applying a fixed patch. It checks the
result compiles and no longer refers to the app, and puts the file back if
not. Everything it touches is first copied to
`daemon/internal_apps/.connectwifi-backup/`.

```bash
python3 setup/whisplay_wifi.py status  ~/Whisplay   # what is there
python3 setup/whisplay_wifi.py remove  ~/Whisplay   # what the installer runs
python3 setup/whisplay_wifi.py restore ~/Whisplay   # what ./uninstall.sh runs
sudo systemctl restart whisplay-daemon              # after either
```

Updating Whisplay with `git pull` conflicts with the edit: restore first,
pull, then run the installer again.

`./register.sh` puts the app back on the desktop without sudo, for
example after moving the folder. `./uninstall.sh` removes the desktop
entry, the sudo rule and the polkit grant, puts back Whisplay's own WiFi
apps if the installer removed them, and restarts the daemon;
`./uninstall.sh --ble` also removes `sugar-wifi-conf`.

### Standalone

The app loads no code from a Whisplay checkout. It speaks
`whisplay-daemon`'s socket protocol itself (`connectwifi/daemon_client.py`:
register, subscribe to events, take the foreground, draw into the shared
RGB565 framebuffer). Its keyboard reader is its own copy
(`connectwifi/keyboard.py`). So removing Whisplay's WiFi apps, examples or
`runtime/` cannot break it. The one exception: with no daemon running at
all, it falls back to driving the HAT directly, and that needs Whisplay's
hardware driver, `runtime/whisplay.py`. `tests/test_daemon_client.py` runs
the app against a fake daemon with no Whisplay on the machine, and checks
that no Whisplay module was loaded.

## Use

Pick **Connect WiFi** on the HAT desktop (hold the button), or run `./run.sh`.

| Input | Menu and lists | Typing a name or password |
|-------|----------------|---------------------------|
| Button, short press | next row | - |
| Button, hold 1 s | select | cancel |
| Button, 4 quick presses | back to the desktop | back to the desktop |
| Up / Down | move | - |
| Enter | select | next / connect |
| Esc | back (from the menu: leave) | back |
| Backspace | - | delete |

- **Scan networks** first shows what NetworkManager already has cached,
  above 40% signal: instant, and the radio stays idle. *Rescan wider
  range* sweeps and lists everything. An empty first pass sweeps on its own.
- **Type hidden network...** takes an SSID, then a password (blank for an
  open network).
- A **blank password** on a secured network reconnects using a profile
  NetworkManager already has. WPA passwords shorter than 8 characters are
  caught on the spot rather than after nmcli times out.
- **Turn BLE on/off** starts or stops the Bluetooth setup service. The
  card above shows the name to look for and the key to enter on the phone.

The app logs to `~/.whisplay-daemon/daemon-app.log`, starting with one
line of what it may do, for example
`[connectwifi] wifi device=wlan0 sudo=False scan=True`.

## What changed from Whisplay's WiFi Config

- **BLE switch works when launched from the desktop.** The original decided
  whether to use sudo by probing `sudo -n true`. Its matching sudoers rule
  allows only `systemctl start|stop sugar-wifi-config.service`, so the probe
  failed, and the app ran plain `systemctl`, which polkit refuses outside a
  login session ("Interactive authentication required"). The app now runs
  `sudo -n systemctl start|stop` directly and falls back to plain systemctl.
- **No background scanning.** The status line polled
  `nmcli device wifi` every 2 seconds without `--rescan no`. nmcli's
  default sweeps whenever its cache is over 30 seconds old, so the radio
  rescanned every 30 seconds while the app was open. Every timed call now
  passes `--rescan no`.
- **Any Wi-Fi interface.** The interface is found through NetworkManager
  instead of assuming `wlan0`.
- **SSIDs containing `:`** are read correctly on the status line too.
- **Standalone.** It loads nothing from the Whisplay checkout: it has its
  own daemon client instead of `runtime/whisplay_client.py`, and its own
  keyboard reader instead of the daemon's `internal_apps/keyboard.py`
  (`connectwifi/keyboard.py`, Apache-2.0, credited in the file). See
  [Standalone](#standalone). Restarting the reader no longer leaves the old
  thread reading. Keypad Enter works.
- **Self-registration is complete.** Run by hand before the installer, it
  still registers a desktop entry that can launch it.
- **Its own app id** (`connectwifi`), so re-running Whisplay's installer,
  which re-seeds the example's entry, cannot overwrite it.
- **Menu shows when BLE is not installed** instead of offering a switch
  that cannot work.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| *Not allowed - run the installer again* | polkit or sudoers is missing a grant: run `./setup.sh` again. |
| Rescan says *cached (no scan permission)* | NetworkManager refuses scans from the daemon: `./setup.sh` adds the polkit grant. Restart `whisplay-daemon` if you were just added to `netdev`. |
| *No networks found* on a Pi | Wi-Fi country not set: `./install-raspberrypi.sh --country GB` (your code). |
| Menu says *NO BLE* | the BLE service is not installed: `./setup.sh` (or it was skipped with `--no-ble`). |
| *no kbd* | no keyboard with a full letter row is plugged in or paired. |
| *the service did not stay up* with `GLIBC_2.39 not found` | installed by an earlier version of this installer; run `./setup.sh` again and it swaps in v2.2.3. |
| The phone cannot find the board | `systemctl status sugar-wifi-config`; on an Orange Pi check `hci0` exists (`ls /sys/class/bluetooth`). |

## Development

```
connectwifi/
  app.py       state machine, workers, main loop
  screens.py   drawing (no hardware), View, RGB565
  network.py   nmcli output parsing and argument lists
  system.py    running commands; when to use sudo
  ble.py       sugar-wifi-conf status and switch
  keyboard.py  evdev keyboard reader
  daemon_client.py  whisplay-daemon protocol: register, events, framebuffer
  board.py     the board: through the daemon, or Whisplay's driver without one
setup/common.sh            shared installer steps
setup/whisplay_wifi.py     removes/restores Whisplay's own WiFi apps
install-raspberrypi.sh     Raspberry Pi steps
install-orangepi.sh        Orange Pi steps
setup.sh  deploy.sh  register.sh  uninstall.sh  run.sh
tests/
```

```bash
python3 -m pytest tests -q                     # 102 tests, no hardware needed
CONNECTWIFI_SCREENSHOTS=/tmp/shots python3 -m pytest tests/test_screens.py
```

The tests drive the app with a fake NetworkManager and systemd, so they run
anywhere. `tests/test_scripts.py` checks the shell scripts parse, and runs
shellcheck when it is installed.

## Credits

The keyboard reader and the app's design come from
[PiSugar/Whisplay](https://github.com/PiSugar/Whisplay) (Apache-2.0).
`sugar-wifi-conf` is [PiSugar's](https://github.com/PiSugar/sugar-wifi-conf),
downloaded from its releases at install time.
# ConnectWifi
