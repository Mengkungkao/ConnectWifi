#!/usr/bin/env bash
#
# Orange Pi installer for Connect WiFi (Whisplay HAT), for Orange Pi OS or
# Armbian on the Zero 2W or Zero 3W. ./setup.sh runs this on an Orange Pi;
# it can also be run directly, as your normal user (it asks for sudo where
# it needs it).
#
#   ./install-orangepi.sh                  walk through every step, asking first
#   ./install-orangepi.sh --yes            accept every prompt (unattended)
#   ./install-orangepi.sh --check          report only; change nothing
#   ./install-orangepi.sh --no-ble         leave out the Bluetooth LE setup service
#   ./install-orangepi.sh --keep-whisplay-wifi   keep Whisplay's own WiFi entries on the desktop
#   ./install-orangepi.sh --ble-name N --ble-key K   name and key the phone sees
#
# What differs from the Raspberry Pi: the Zero 2W's Bluetooth (UWE5622)
# comes up through a vendor service rather than hciuart; Ubuntu 22.04's
# polkit is 0.105, which reads .pkla files and not JavaScript rules
# (setup/common.sh writes whichever the board reads); and there is no
# vcgencmd, so the BLE info panel reads the temperature from sysfs.
set -uo pipefail

STEPS=9
# shellcheck source=setup/common.sh
. "$(dirname "$(readlink -f "$0")")/setup/common.sh"

# Services that attach the Zero 2W/3W's Bluetooth chip, by image.
BT_VENDOR_UNITS="sprd-bluetooth.service aw859a-bluetooth.service"

board_bluetooth_hint() {
    local unit
    for unit in $BT_VENDOR_UNITS; do
        if systemctl cat "$unit" >/dev/null 2>&1; then
            info "$unit brings up this board's Bluetooth: sudo systemctl enable --now $unit"
            return
        fi
    done
    info "enable Bluetooth with sudo orangepi-config (System -> Hardware), then reboot"
}

# ---------------------------------------------------------------- 1. host
step "Checking the host"
check_host
case "$MODEL" in
    *[Oo]range*) ;;
    *) warn "this is not an Orange Pi; ./setup.sh picks the right installer" ;;
esac

# ------------------------------------------------------------ 2. packages
step "System packages"
install_packages python3-pil python3-numpy fonts-dejavu-core bluez curl rfkill

# ------------------------------------------------------------ 3. whisplay
step "Whisplay HAT"
check_whisplay

# -------------------------------------------------------------- 4. radios
step "Wi-Fi and Bluetooth radios"
for kind in wifi bluetooth; do
    if "$RFKILL" list "$kind" 2>/dev/null | grep -q "Soft blocked: yes"; then
        warn "$kind is soft-blocked by rfkill"
        ask "unblock it?" && sudo rfkill unblock "$kind" && ok "unblocked"
    fi
done
for unit in $BT_VENDOR_UNITS; do
    systemctl cat "$unit" >/dev/null 2>&1 || continue
    if systemctl is-active --quiet "$unit" || systemctl is-enabled --quiet "$unit"; then
        ok "$unit is enabled"
    elif [ -e /sys/class/bluetooth/hci0 ]; then
        ok "Bluetooth is up without $unit"
    else
        warn "$unit is off and there is no Bluetooth controller"
        ask "enable it?" && sudo systemctl enable --now "$unit" && ok "enabled"
    fi
done
if [ -e /sys/class/bluetooth/hci0 ]; then
    ok "Bluetooth controller hci0"
else
    warn "no Bluetooth controller yet"
fi
if systemctl is-active --quiet bluetooth; then
    ok "bluetooth.service is running"
else
    warn "bluetooth.service is not running"
    ask "start it?" && sudo systemctl enable --now bluetooth && ok "started"
fi

# ------------------------------------------------------ 5. NetworkManager
step "NetworkManager"
if check_networkmanager; then
    check_nm_permissions
    keep_wifi_up
else
    info "Orange Pi OS and Armbian ship NetworkManager; install it with"
    info "sudo apt install network-manager, or turn it on in orangepi-config / armbian-config."
fi

# ------------------------------------------------------------- 6. BLE
step "Bluetooth LE Wi-Fi setup (sugar-wifi-conf)"
install_ble_service

# ------------------------------------------------------------ 7. sudo
step "The app's BLE switch"
if [ "$SKIP_BLE" = 1 ] || [ ! -f "$BLE_UNIT" ]; then
    info "skipped: no BLE service to switch"
else
    install_sudo_rule
fi

# -------------------------------------------------------------- 8. app
step "Adding the app to the HAT desktop"
register_with_daemon

# --------------------------------------------------- 9. Whisplay's WiFi
step "Putting Connect WiFi in place of Whisplay's WiFi"
replace_whisplay_wifi
restart_daemon_if_needed
run_selftest

finish
