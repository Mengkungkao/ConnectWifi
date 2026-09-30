#!/usr/bin/env bash
#
# Raspberry Pi installer for Connect WiFi (Whisplay HAT), for Raspberry Pi
# OS Bookworm or later. ./setup.sh runs this on a Pi; it can also be run
# directly, as your normal user (it asks for sudo where it needs it).
#
#   ./install-raspberrypi.sh                  walk through every step, asking first
#   ./install-raspberrypi.sh --yes            accept every prompt (unattended)
#   ./install-raspberrypi.sh --check          report only; change nothing
#   ./install-raspberrypi.sh --country GB     set the Wi-Fi country
#   ./install-raspberrypi.sh --no-ble         leave out the Bluetooth LE setup service
#   ./install-raspberrypi.sh --keep-whisplay-wifi   keep Whisplay's own WiFi entries on the desktop
#   ./install-raspberrypi.sh --ble-name N --ble-key K   name and key the phone sees
#
# What differs from the Orange Pi: Raspberry Pi OS keeps the Wi-Fi radio
# blocked until a Wi-Fi country is set; before Bookworm it ran dhcpcd, not
# NetworkManager; and dtoverlay=disable-wifi or disable-bt in config.txt
# switches a radio off before Linux ever sees it.
set -uo pipefail

STEPS=9
# shellcheck source=setup/common.sh
. "$(dirname "$(readlink -f "$0")")/setup/common.sh"

board_bluetooth_hint() {
    info "Check config.txt has no dtoverlay=disable-bt and that hciuart.service"
    info "is running (sudo systemctl enable --now hciuart), then reboot."
}

# ---------------------------------------------------------------- 1. host
step "Checking the host"
check_host
case "$MODEL" in
    *"Raspberry Pi"*) ;;
    *) warn "this is not a Raspberry Pi; ./setup.sh picks the right installer" ;;
esac

# ------------------------------------------------------------ 2. packages
step "System packages"
install_packages python3-pil fonts-dejavu-core bluez curl rfkill

# ------------------------------------------------------------ 3. whisplay
step "Whisplay HAT"
check_whisplay

# -------------------------------------------------------------- 4. radios
step "Wi-Fi and Bluetooth radios"
CONFIG_TXT=/boot/firmware/config.txt
[ -f "$CONFIG_TXT" ] || CONFIG_TXT=/boot/config.txt
RADIOS_OFF=0
for overlay in disable-wifi disable-bt; do
    if grep -qE "^[[:space:]]*dtoverlay=$overlay([[:space:]]|,|$)" "$CONFIG_TXT" 2>/dev/null; then
        RADIOS_OFF=1
        warn "$CONFIG_TXT has dtoverlay=$overlay"
        if ask "remove it? (takes effect after a reboot)"; then
            sudo sed -i -E "/^[[:space:]]*dtoverlay=$overlay([[:space:]]|,|$)/d" "$CONFIG_TXT" \
                && ok "removed -- reboot, then run this again" || bad "could not edit $CONFIG_TXT"
        else
            bad "that radio stays off"
        fi
    fi
done
[ "$RADIOS_OFF" = 0 ] && ok "no radio is switched off in $CONFIG_TXT"

CURRENT_COUNTRY=$(raspi-config nonint get_wifi_country 2>/dev/null || true)
if [ -n "$COUNTRY" ]; then
    if [ "$CHECK_ONLY" = 1 ]; then
        info "(check only: would set the Wi-Fi country to $COUNTRY)"
    elif sudo raspi-config nonint do_wifi_country "$COUNTRY"; then
        ok "Wi-Fi country set to $COUNTRY"
    else
        bad "raspi-config could not set the country to $COUNTRY"
    fi
elif [ -n "$CURRENT_COUNTRY" ]; then
    ok "Wi-Fi country: $CURRENT_COUNTRY"
else
    warn "no Wi-Fi country set: Raspberry Pi OS keeps the radio blocked until there is one"
    info "run again with --country XX (your two-letter country code, e.g. GB, US, TH)"
fi

# ------------------------------------------------------ 5. NetworkManager
step "NetworkManager"
if check_networkmanager; then
    check_nm_permissions
    keep_wifi_up
else
    info "Bookworm and later use NetworkManager. On an older image, switch with:"
    info "sudo raspi-config  ->  Advanced Options  ->  Network Config  ->  NetworkManager"
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
