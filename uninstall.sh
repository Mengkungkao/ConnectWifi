#!/usr/bin/env bash
# Remove what the installer added: the desktop entry, the sudo rule, the
# polkit grant, the Wi-Fi keep-alive and the power-saving override. Put
# Whisplay's own WiFi entries back on the desktop if the installer took them
# off. The BLE service is left alone unless --ble is given, since a Whisplay
# image ships it too.
#
#   ./uninstall.sh          remove the app; list Whisplay's WiFi again
#   ./uninstall.sh --ble    also stop and remove sugar-wifi-conf
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 1

REMOVE_BLE=0
[ "${1:-}" = "--ble" ] && REMOVE_BLE=1
RESTART=0

rm -f "$HOME/.whisplay-daemon/app/connectwifi.json" \
    && echo "removed the desktop entry"
sudo rm -f /etc/sudoers.d/connectwifi \
    /etc/polkit-1/rules.d/50-connectwifi-networkmanager.rules \
    /etc/polkit-1/localauthority/50-local.d/50-connectwifi-networkmanager.pkla \
    && echo "removed the sudo rule and polkit grant"

sudo systemctl disable --now connectwifi-keepalive.service 2>/dev/null
if sudo rm -f /etc/systemd/system/connectwifi-keepalive.service \
        /etc/NetworkManager/conf.d/zz-connectwifi-wifi-powersave-off.conf; then
    sudo systemctl daemon-reload
    sudo systemctl reload NetworkManager 2>/dev/null
    echo "removed the Wi-Fi keep-alive and the power-saving override"
fi

# The Whisplay checkout the daemon runs from, as the installer found it.
script=$(systemctl show -p ExecStart --value whisplay-daemon 2>/dev/null |
         grep -oE '[^ ;=]+/daemon/whisplay_daemon\.py' | head -1)
if [ -n "$script" ]; then
    root=$(dirname "$(dirname "$script")")
    runner=()
    [ -w "$root/daemon/internal_apps/manager.py" ] || runner=(sudo env "HOME=$HOME")
    if out=$("${runner[@]}" python3 setup/whisplay_wifi.py restore "$root" 2>&1); then
        if [ "$out" != "nothing to do" ]; then
            echo "$out"
            RESTART=1
        fi
    else
        echo "could not put back Whisplay's WiFi entries: $out" >&2
    fi
fi

if [ "$REMOVE_BLE" = 1 ]; then
    sudo systemctl disable --now sugar-wifi-config.service 2>/dev/null
    sudo rm -f /etc/systemd/system/sugar-wifi-config.service
    sudo rm -rf /opt/sugar-wifi-config
    sudo systemctl daemon-reload
    echo "removed sugar-wifi-conf"
fi

# The daemon reads its app list when it starts; the desktop entry and any
# restored Whisplay app take effect then.
if [ "$RESTART" = 1 ]; then
    sudo systemctl restart whisplay-daemon && echo "restarted whisplay-daemon"
else
    echo "Connect WiFi leaves the desktop at the next reboot or: sudo systemctl restart whisplay-daemon"
fi
