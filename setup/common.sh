# shellcheck shell=bash
# Shared by install-raspberrypi.sh and install-orangepi.sh; not run directly.
#
# Each board installer sets STEPS, sources this file (which parses the
# command line and moves to the project root), then runs its steps with
# the helpers below. Everything that is the same on both boards lives
# here so the two installers cannot drift apart.

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." || exit 1
HERE="$(pwd)"

BLE_SERVICE=sugar-wifi-config.service
BLE_DIR=/opt/sugar-wifi-config
BLE_UNIT=/etc/systemd/system/$BLE_SERVICE
SUDOERS_FILE=/etc/sudoers.d/connectwifi
POLKIT_RULE=/etc/polkit-1/rules.d/50-connectwifi-networkmanager.rules
POLKIT_PKLA=/etc/polkit-1/localauthority/50-local.d/50-connectwifi-networkmanager.pkla
DAEMON_SOCKET=/tmp/whisplay-daemon.sock

ASSUME_YES=0
CHECK_ONLY=0
SKIP_BLE=0
KEEP_WHISPLAY_WIFI=0
BLE_NAME=""
BLE_KEY=""
# shellcheck disable=SC2034  # read by install-raspberrypi.sh
COUNTRY=""
FAILURES=0
STEP=0
DAEMON_RESTART_NEEDED=0

usage() {
    # The calling script's header comment, minus the leading "# ".
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

# shellcheck disable=SC2034  # COUNTRY is read by install-raspberrypi.sh
while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)    ASSUME_YES=1 ;;
        -n|--check)  CHECK_ONLY=1 ;;
        --no-ble)    SKIP_BLE=1 ;;
        --keep-whisplay-wifi) KEEP_WHISPLAY_WIFI=1 ;;
        --ble-name)  BLE_NAME="${2:-}"; shift ;;
        --ble-key)   BLE_KEY="${2:-}"; shift ;;
        --country)   COUNTRY="${2:-}"; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 1 ;;
    esac
    shift
done

# The name and key end up on a systemd ExecStart line and in a regex on
# the app's side, so keep them to characters that need no quoting.
for value in "$BLE_NAME" "$BLE_KEY"; do
    if [ -n "$value" ] && ! [[ "$value" =~ ^[A-Za-z0-9._-]{1,32}$ ]]; then
        echo "--ble-name and --ble-key take 1-32 of A-Z a-z 0-9 . _ -" >&2
        exit 1
    fi
done

if [ "$(id -u)" = 0 ]; then
    echo "Run this as your normal user, not with sudo: it registers the app" >&2
    echo "for that user and asks for sudo itself where it needs it." >&2
    exit 1
fi

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)

step() { STEP=$((STEP + 1)); echo; echo "${BOLD}==> ${STEP}/${STEPS}  $*${RESET}"; }
ok()   { echo "    ${GREEN}ok${RESET}   $*"; }
warn() { echo "    ${YELLOW}warn${RESET} $*"; }
bad()  { echo "    ${RED}fail${RESET} $*"; FAILURES=$((FAILURES + 1)); }
info() { echo "         $*"; }

ask() {
    # ask "question"  -> 0 for yes
    [ "$CHECK_ONLY" = 1 ] && { info "(check only: skipping)"; return 1; }
    [ "$ASSUME_YES" = 1 ] && return 0
    read -r -p "         $1 [y/N] " reply
    [[ "$reply" =~ ^[Yy] ]]
}

SYSTEMCTL=$(readlink -f "$(command -v systemctl)")
# Debian leaves /usr/sbin off a normal user's PATH; listing works without root.
RFKILL=$(command -v rfkill || echo /usr/sbin/rfkill)

# ---------------------------------------------------------------- host
check_host() {
    MODEL=""
    if [ -f /proc/device-tree/model ]; then
        MODEL=$(tr -d '\0' < /proc/device-tree/model)
        ok "$MODEL"
    else
        warn "no device tree model -- is this the right board?"
    fi
    info "$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") · python $(python3 -V 2>&1 | cut -d' ' -f2) · $(uname -m)"
}

# ------------------------------------------------------------ packages
install_packages() {
    # install_packages pkg...
    local missing=() pkg
    for pkg in "$@"; do
        if dpkg -s "$pkg" >/dev/null 2>&1; then
            ok "$pkg"
        else
            warn "$pkg missing"
            missing+=("$pkg")
        fi
    done
    [ ${#missing[@]} -eq 0 ] && return
    info "needed: ${missing[*]}"
    if ask "install them with apt now?"; then
        if sudo apt-get update -qq && sudo apt-get install -y "${missing[@]}"; then
            ok "installed"
        else
            bad "apt install failed"
        fi
    else
        bad "install manually: sudo apt install ${missing[*]}"
    fi
}

# -------------------------------------------------------------- whisplay
check_whisplay() {
    # The app needs whisplay-daemon running and nothing else from Whisplay:
    # it speaks the daemon's socket protocol itself (connectwifi/daemon_client.py).
    if python3 -c 'import sys; sys.path.insert(0, "."); from connectwifi.daemon_client import daemon_running; sys.exit(0 if daemon_running() else 1)'; then
        ok "whisplay-daemon is running and answering on $DAEMON_SOCKET"
    elif systemctl is-active --quiet whisplay-daemon; then
        bad "whisplay-daemon is running but not answering on $DAEMON_SOCKET"
    else
        bad "whisplay-daemon is not running; it owns the HAT's screen and button. Install it with:"
        info "git clone https://github.com/PiSugar/Whisplay ~/Whisplay"
        info "cd ~/Whisplay && sudo bash install_driver.sh   (then reboot)"
        info "sudo bash daemon/install_whisplay_daemon_service.sh"
    fi
}

# ------------------------------------------------------ NetworkManager
check_networkmanager() {
    if ! command -v nmcli >/dev/null 2>&1; then
        bad "nmcli not found: this app joins networks through NetworkManager"
        return 1
    fi
    if ! systemctl is-active --quiet NetworkManager; then
        bad "NetworkManager is installed but not running"
        return 1
    fi
    WIFI_DEVICE=$(nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2 == "wifi" { print $1; exit }')
    if [ -z "$WIFI_DEVICE" ]; then
        bad "NetworkManager manages no Wi-Fi device"
        return 1
    fi
    local state
    state=$(nmcli -t -f DEVICE,STATE device | awk -F: -v d="$WIFI_DEVICE" '$1 == d { print $2 }')
    ok "Wi-Fi device $WIFI_DEVICE ($state)"
    if [ "$state" = unavailable ] && "$RFKILL" list wifi 2>/dev/null | grep -q "Soft blocked: yes"; then
        warn "Wi-Fi is soft-blocked by rfkill"
        ask "unblock it?" && sudo rfkill unblock wifi && ok "unblocked"
    fi
}

sessionless_nm_allowed() {
    # Whether NetworkManager lets a process outside any login session (as
    # whisplay-daemon's children are) scan and connect. A systemd --user
    # unit has no session either, so ask from there. 2 = could not tell.
    local out
    out=$(systemd-run --user --pipe --wait --quiet \
          nmcli -t -f permission,value general permissions 2>/dev/null) || return 2
    [ -n "$out" ] || return 2
    echo "$out" | grep -q '^org.freedesktop.NetworkManager.wifi.scan:yes$' &&
        echo "$out" | grep -q '^org.freedesktop.NetworkManager.network-control:yes$'
}

install_polkit_rule() {
    # polkit before 0.106 (Ubuntu 22.04, Debian 11) reads .pkla files; later
    # versions read JavaScript rules only. Write whichever this one reads.
    local version
    version=$(pkaction --version 2>/dev/null | awk '{ print $NF }')
    if [ -z "$version" ]; then
        bad "polkit not found; NetworkManager decides on its own"
        return
    fi
    if printf '%s\n' "$version" | grep -q '^0\.10[0-5]\b'; then
        sudo install -d -m 0755 "$(dirname "$POLKIT_PKLA")"
        sudo tee "$POLKIT_PKLA" >/dev/null <<'EOF'
# Written by ConnectWifi's installer. The app runs under whisplay-daemon,
# outside any login session; this lets the netdev group scan and join
# Wi-Fi from there.
[ConnectWifi: netdev controls NetworkManager]
Identity=unix-group:netdev
Action=org.freedesktop.NetworkManager.*
ResultAny=yes
ResultInactive=yes
ResultActive=yes
EOF
        sudo chmod 0644 "$POLKIT_PKLA"
        ok "polkit $version: wrote $POLKIT_PKLA"
    else
        sudo install -d -m 0755 "$(dirname "$POLKIT_RULE")"
        sudo tee "$POLKIT_RULE" >/dev/null <<'EOF'
// Written by ConnectWifi's installer. The app runs under whisplay-daemon,
// outside any login session; this lets the netdev group scan and join
// Wi-Fi from there.
polkit.addRule(function(action, subject) {
  if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 && subject.isInGroup("netdev")) {
    return polkit.Result.YES;
  }
});
EOF
        sudo chmod 0644 "$POLKIT_RULE"
        ok "polkit $version: wrote $POLKIT_RULE"
    fi
}

check_nm_permissions() {
    if id -nG "$USER" | tr ' ' '\n' | grep -qx netdev; then
        ok "$USER is in the netdev group"
    else
        warn "$USER is not in the netdev group"
        if ask "add $USER to netdev?"; then
            getent group netdev >/dev/null || sudo groupadd --system netdev
            sudo usermod -aG netdev "$USER" && ok "added"
            DAEMON_RESTART_NEEDED=1
        else
            bad "the app cannot scan or join without it"
        fi
    fi

    sessionless_nm_allowed
    case $? in
        0) ok "NetworkManager allows scan and connect from the daemon"; return ;;
        1) warn "NetworkManager refuses scan/connect outside a login session" ;;
        *) info "could not ask NetworkManager from outside this session; adding the rule to be sure" ;;
    esac
    if ask "add a polkit rule that lets netdev control NetworkManager?"; then
        install_polkit_rule
        [ "$CHECK_ONLY" = 0 ] && sessionless_nm_allowed && ok "now allowed"
    else
        bad "the app will only see cached scans and cannot connect"
    fi
}

# ----------------------------------------------------- staying on Wi-Fi
NM_POWERSAVE_CONF=/etc/NetworkManager/conf.d/zz-connectwifi-wifi-powersave-off.conf
KEEPALIVE=connectwifi-keepalive

wifi_powersave() {
    # wifi.powersave once every config file is read: 2 off, 3 on, empty
    # when it is left to the driver.
    local nm
    nm=$(command -v NetworkManager || echo /usr/sbin/NetworkManager)
    "$nm" --print-config 2>/dev/null | sed -n 's/^wifi\.powersave=//p' | tail -1
}

keep_wifi_up() {
    case "$(wifi_powersave)" in
        3)
            warn "Wi-Fi power saving is on"
            info "On the Orange Pi Zero 2W it gets the board dropped by the router. The"
            info "image means to turn it off (20-override-wifi-powersave-disable.conf), but"
            info "NetworkManager reads config files alphabetically, so Ubuntu's"
            info "default-wifi-powersave-on.conf comes last and turns it back on."
            info "Off costs some battery."
            if ask "turn Wi-Fi power saving off?"; then
                printf '%s\n' "# Written by ConnectWifi installer. Named to sort after" \
                    "# default-wifi-powersave-on.conf, which would otherwise be read last." \
                    "[connection]" "wifi.powersave = 2" | sudo tee "$NM_POWERSAVE_CONF" >/dev/null &&
                    sudo systemctl reload NetworkManager && ok "wrote $NM_POWERSAVE_CONF"
                # NetworkManager applies it at the next connect; iw applies it now,
                # without dropping the connection.
                local iw
                iw=$(command -v iw || echo /usr/sbin/iw)
                if [ -n "${WIFI_DEVICE:-}" ] && [ -x "$iw" ]; then
                    sudo "$iw" dev "$WIFI_DEVICE" set power_save off && ok "off on $WIFI_DEVICE from now"
                fi
            else
                warn "left on"
            fi
            ;;
        2) ok "Wi-Fi power saving is off" ;;
        *) ok "Wi-Fi power saving is left to the driver" ;;
    esac

    local service=/etc/systemd/system/$KEEPALIVE.service
    if systemctl is-enabled --quiet "$KEEPALIVE.service" 2>/dev/null &&
       grep -qx "WorkingDirectory=$HERE" "$service" 2>/dev/null; then
        ok "the Wi-Fi keep-alive is on (checks every minute)"
        return
    fi
    info "When Wi-Fi drops and a failed reconnect looks like a wrong password,"
    info "NetworkManager waits for a new one and stops trying. The keep-alive"
    info "brings the saved network back within a minute or two. It runs as $USER."
    ask "turn on the Wi-Fi keep-alive?" || { info "skipped"; return; }
    # One long-running process rather than a timer: a timer's job would put
    # "Starting"/"Finished" in the journal every minute.
    sudo tee "$service" >/dev/null <<EOF
[Unit]
Description=ConnectWifi: bring Wi-Fi back when NetworkManager gives up
After=NetworkManager.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$HERE
ExecStart=$(command -v python3) -m connectwifi.keepalive
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=30
Nice=10

[Install]
WantedBy=multi-user.target
EOF
    if sudo systemctl daemon-reload && sudo systemctl enable --now "$KEEPALIVE.service" >/dev/null 2>&1 &&
       sleep 2 && systemctl is-active --quiet "$KEEPALIVE.service"; then
        ok "the Wi-Fi keep-alive is running"
    else
        bad "the keep-alive did not start:"
        journalctl -u "$KEEPALIVE.service" -n 6 --no-pager 2>/dev/null | sed 's/^/           /'
    fi
}

# ------------------------------------------------------------ BLE service
ble_unit_arg() {
    # ble_unit_arg --name -> the value in the installed unit, if any
    sed -n "s/.*$1[= ]\+\([^ ]\+\).*/\1/p" "$BLE_UNIT" 2>/dev/null | head -1
}

random_key() {
    # 8 characters with nothing that reads two ways on a small screen.
    LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' </dev/urandom 2>/dev/null | head -c 8
}

ble_arch() {
    case "$(uname -m)" in
        aarch64|arm64) echo aarch64 ;;
        armv7*)        echo armv7 ;;
        armv6*)        echo armv6 ;;
        *)             return 1 ;;
    esac
}

download() {
    # download OUTPUT URL... -- first mirror that answers wins
    local output=$1 url
    shift
    for url in "$@"; do
        curl -fsSL --retry 3 --connect-timeout 20 "$url" -o "$output" && return 0
    done
    return 1
}

# Releases to try, newest first. v2.3.0's builds need glibc 2.39 (Ubuntu
# 24.04, Debian 13), so on Ubuntu 22.04 -- Orange Pi OS -- it dies in the
# loader. v2.2.3, the release before it, needs glibc 2.30.
BLE_RELEASES="latest v2.2.3"

ble_release_urls() {
    # ble_release_urls RELEASE ARCH -> the PiSugar mirror, then GitHub
    local path base
    if [ "$1" = latest ]; then
        path="releases/latest/download/sugar-wifi-conf-$2"
    else
        path="releases/download/$1/sugar-wifi-conf-$2"
    fi
    for base in https://repo.pisugar.uk https://github.com; do
        echo "$base/PiSugar/sugar-wifi-conf/$path"
    done
}

is_elf() {
    [ "$(head -c 4 "$1" | od -An -tx1 | tr -d ' ')" = 7f454c46 ]
}

runs_here() {
    # A build for a newer glibc fails in the dynamic loader before main(),
    # so --help is enough to tell, and does nothing else.
    "$1" --help >/dev/null 2>&1
}

needed_glibc() {
    grep -aoE 'GLIBC_2\.[0-9]+' "$1" 2>/dev/null | sort -uV | tail -1 | sed 's/_/ /'
}

fetch_ble_binary() {
    # fetch_ble_binary OUTPUT ARCH -- the newest release that runs on this
    # system; sets BLE_RELEASE to the one chosen.
    local output=$1 arch=$2 release
    local -a urls
    BLE_RELEASE=""
    for release in $BLE_RELEASES; do
        mapfile -t urls < <(ble_release_urls "$release" "$arch")
        if ! download "$output" "${urls[@]}"; then
            warn "could not download sugar-wifi-conf $release"
            continue
        fi
        if ! is_elf "$output"; then
            warn "sugar-wifi-conf $release: the download is not a program (a captive portal?)"
            continue
        fi
        chmod 0755 "$output"
        if runs_here "$output"; then
            BLE_RELEASE=$release
            return 0
        fi
        warn "sugar-wifi-conf $release needs $(needed_glibc "$output"); this system has $(getconf GNU_LIBC_VERSION)"
    done
    return 1
}

install_ble_service() {
    if [ "$SKIP_BLE" = 1 ]; then
        info "skipped (--no-ble)"
        return
    fi
    if ! [ -e /sys/class/bluetooth/hci0 ] && ! timeout 5 bluetoothctl list 2>/dev/null | grep -q Controller; then
        warn "no Bluetooth controller found; BLE setup needs one"
        board_bluetooth_hint
        return
    fi
    ok "Bluetooth controller present"

    # Keep a name and key already in use: a phone may know them. A unit that
    # passes neither runs on sugar-wifi-conf's defaults, raspberrypi/pisugar.
    # A new install advertises the hostname, with a key of its own rather
    # than the default everyone knows; the app shows both on its menu.
    local name key installed=0 old_name="" old_key=""
    if [ -f "$BLE_UNIT" ]; then
        installed=1
        old_name=$(ble_unit_arg --name); old_name=${old_name:-raspberrypi}
        old_key=$(ble_unit_arg --key);   old_key=${old_key:-pisugar}
    fi
    name=${BLE_NAME:-${old_name:-$(hostname)}}
    key=${BLE_KEY:-${old_key:-$(random_key)}}

    # An install that cannot run -- say the newest release on too old a
    # glibc -- gets its program replaced.
    local binary_ok=0
    [ -x "$BLE_DIR/sugar-wifi-conf" ] && runs_here "$BLE_DIR/sugar-wifi-conf" && binary_ok=1
    if [ "$installed" = 1 ] && [ -x "$BLE_DIR/sugar-wifi-conf" ] && [ "$binary_ok" = 0 ]; then
        warn "the installed sugar-wifi-conf cannot run here: it needs" \
             "$(needed_glibc "$BLE_DIR/sugar-wifi-conf"), this system has $(getconf GNU_LIBC_VERSION)"
        info "replacing it with a release that runs, keeping its name and key"
    elif [ "$installed" = 1 ] && [ "$binary_ok" = 1 ]; then
        ok "sugar-wifi-conf is installed ($BLE_UNIT)"
        if [ "$name" = "$old_name" ] && [ "$key" = "$old_key" ]; then
            systemctl is-enabled --quiet "$BLE_SERVICE" && ok "enabled at boot" || warn "not enabled at boot"
            systemctl is-active --quiet "$BLE_SERVICE" && ok "running" || info "not running (the app can start it)"
            return
        fi
        info "changing its name/key to $name / $key"
    else
        info "PiSugar's sugar-wifi-conf lets a phone send the Wi-Fi name and password"
        if [ -n "$BLE_KEY" ] || [ "$CHECK_ONLY" = 0 ]; then
            info "over Bluetooth LE. It will advertise as '$name' with key '$key'."
        else
            # Each run draws a new key, so this one would never be the real one.
            info "over Bluetooth LE. It will advertise as '$name' with a new random key."
        fi
    fi
    ask "install/update the BLE setup service?" || { info "skipped"; return; }

    local arch tmp
    if ! arch=$(ble_arch); then
        bad "no sugar-wifi-conf build for $(uname -m)"
        return
    fi
    if [ "$binary_ok" = 0 ]; then
        # In the project folder, not /tmp: /tmp may be mounted noexec, and
        # the download has to be run once to prove it works here.
        tmp=$(mktemp "$HERE/.sugar-wifi-conf.XXXXXX")
        if ! fetch_ble_binary "$tmp" "$arch"; then
            rm -f "$tmp"
            bad "no sugar-wifi-conf release runs on this system"
            return
        fi
        sudo install -d -m 0755 "$BLE_DIR"
        sudo install -m 0755 "$tmp" "$BLE_DIR/sugar-wifi-conf"
        rm -f "$tmp"
        ok "installed $BLE_DIR/sugar-wifi-conf ($BLE_RELEASE, $arch)"
    fi
    # The info panel the phone shows. Ours reads the temperature from sysfs,
    # which works on every board; PiSugar's calls vcgencmd, a Pi-only tool.
    if [ ! -f "$BLE_DIR/custom_config.json" ]; then
        sudo install -m 0644 "$HERE/setup/ble-info.json" "$BLE_DIR/custom_config.json"
    fi

    sudo tee "$BLE_UNIT" >/dev/null <<EOF
[Unit]
Description=Sugar WiFi Configuration Service (BLE Wi-Fi setup)
After=network.target bluetooth.target
Wants=bluetooth.target
# A program that cannot start gives up after 5 tries, not every 5 s forever.
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
ExecStartPre=-/usr/sbin/rfkill unblock bluetooth
ExecStart=$BLE_DIR/sugar-wifi-conf --name $name --key $key --config $BLE_DIR/custom_config.json
WorkingDirectory=$BLE_DIR
Restart=always
RestartSec=5
User=root
Environment=RUST_LOG=info

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    # restart, not enable --now: a running service must pick up a new name/key.
    # reset-failed: a service that hit its start limit refuses to restart.
    sudo systemctl reset-failed "$BLE_SERVICE" >/dev/null 2>&1
    if sudo systemctl enable "$BLE_SERVICE" >/dev/null 2>&1 &&
       sudo systemctl restart "$BLE_SERVICE" && sleep 3 &&
       systemctl is-active --quiet "$BLE_SERVICE"; then
        ok "running as '$name', key '$key'"
    else
        bad "the service did not stay up:"
        journalctl -u "$BLE_SERVICE" -n 8 --no-pager 2>/dev/null | sed 's/^/           /'
    fi
}

# ------------------------------------------------------------ sudo rule
ble_switch_passwordless() {
    # Whether sudoers already lets the app start and stop the BLE service
    # without a password. `sudo -l COMMAND` cannot tell: it answers whether
    # the command is allowed at all, and with a password everything is. So
    # read the NOPASSWD rules. -k ignores a ticket cached by the sudo calls
    # earlier in this run; without any NOPASSWD rule the listing itself
    # wants a password, and -n makes that a no.
    local nopasswd
    nopasswd=$(sudo -k -n -l 2>/dev/null | grep 'NOPASSWD:') || return 1
    echo "$nopasswd" | grep -qE 'NOPASSWD:([[:space:]]*[A-Z_]+:)*[[:space:]]*ALL[[:space:]]*$' && return 0
    echo "$nopasswd" | grep -qF "systemctl start $BLE_SERVICE" &&
        echo "$nopasswd" | grep -qF "systemctl stop $BLE_SERVICE"
}

install_sudo_rule() {
    # Exactly the two commands the app's BLE switch runs, and nothing else.
    local rule
    rule="$USER ALL=(root) NOPASSWD: $SYSTEMCTL start $BLE_SERVICE, $SYSTEMCTL stop $BLE_SERVICE"
    if ble_switch_passwordless; then
        ok "the app may start and stop $BLE_SERVICE without a password"
        return
    fi
    info "the app's 'Turn BLE on/off' needs: $rule"
    ask "add it as $SUDOERS_FILE?" || { warn "the BLE switch will say 'Not allowed'"; return; }
    local tmp
    tmp=$(mktemp)
    printf '# Written by ConnectWifi installer: the BLE switch in the app.\n%s\n' "$rule" > "$tmp"
    if sudo visudo -cf "$tmp" >/dev/null; then
        sudo install -o root -g root -m 0440 "$tmp" "$SUDOERS_FILE" && ok "wrote $SUDOERS_FILE"
    else
        bad "visudo rejected the rule; nothing was written"
    fi
    rm -f "$tmp"
}

# --------------------------------------------------------------- daemon
register_with_daemon() {
    if [ ! -S "$DAEMON_SOCKET" ]; then
        warn "whisplay-daemon is not listening; start the app with ./run.sh instead"
        return
    fi
    if [ "$CHECK_ONLY" = 1 ]; then
        info "(check only: skipping)"
        return
    fi
    local out
    if out=$(./register.sh 2>&1); then
        ok "'Connect WiFi' is on the HAT desktop"
    else
        bad "$out"
    fi
}

whisplay_dir() {
    # The Whisplay checkout the running daemon comes from, read from its
    # unit's ExecStart (.../daemon/whisplay_daemon.py), so the copy edited
    # is the copy that runs.
    local script
    script=$(systemctl show -p ExecStart --value whisplay-daemon 2>/dev/null |
             grep -oE '[^ ;=]+/daemon/whisplay_daemon\.py' | head -1)
    [ -n "$script" ] && [ -f "$script" ] && dirname "$(dirname "$script")"
}

replace_whisplay_wifi() {
    # Whisplay's built-in "WiFi" and its example "WiFi Config" do what this
    # app does, and Connect WiFi sits in the built-in's place on the desktop.
    # setup/whisplay_wifi.py takes only their menu entries off; Whisplay's
    # code stays. ./uninstall.sh puts the entries back.
    if [ "$KEEP_WHISPLAY_WIFI" = 1 ]; then
        info "kept (--keep-whisplay-wifi)"
        return
    fi
    local root state
    if ! root=$(whisplay_dir); then
        warn "could not find the Whisplay checkout whisplay-daemon runs from; skipped"
        return
    fi
    if ! state=$(python3 setup/whisplay_wifi.py status "$root" 2>&1); then
        warn "$state"
        return
    fi
    if [ "$state" = "built-in WiFi: hidden; example WiFi Config: hidden" ]; then
        ok "Whisplay's own WiFi entries are off the desktop ($root)"
        return
    fi
    info "$root -- $state"
    info "Connect WiFi takes their place. Only the menu entries go: one line of"
    info "daemon/internal_apps/manager.py, backed up; Whisplay's WiFi code stays."
    ask "take Whisplay's own WiFi entries off the desktop?" || { info "kept"; return; }
    local out runner=()
    [ -w "$root/daemon/internal_apps/manager.py" ] || runner=(sudo env "HOME=$HOME")
    if out=$("${runner[@]}" python3 setup/whisplay_wifi.py hide "$root" 2>&1); then
        echo "$out" | sed 's/^/    ok   /'
        DAEMON_RESTART_NEEDED=1
    else
        bad "$out"
        info "Whisplay was left as it was."
    fi
}

restart_daemon_if_needed() {
    [ "$DAEMON_RESTART_NEEDED" = 1 ] || return 0
    info "whisplay-daemon must restart to pick up the change (this closes the running app)."
    if ask "restart it now?"; then
        sudo systemctl restart whisplay-daemon && ok "restarted" || bad "restart failed"
    else
        info "later: sudo systemctl restart whisplay-daemon   (or reboot)"
    fi
}

# ----------------------------------------------------------------- test
run_selftest() {
    local log
    log=$(mktemp)
    if python3 -m pytest tests -q -p no:cacheprovider >"$log" 2>&1; then
        ok "$(tail -1 "$log")"
    elif python3 -m pytest --version >/dev/null 2>&1; then
        bad "tests failed:"
        tail -20 "$log" | sed 's/^/           /'
    else
        warn "pytest is not installed; skipped (sudo apt install python3-pytest)"
    fi
    rm -f "$log"
}

finish() {
    echo
    if [ "$CHECK_ONLY" = 1 ]; then
        echo "${BOLD}Check finished with $FAILURES issue(s); nothing was changed.${RESET}"
        echo "Run without --check to install what is skipped above."
        return
    elif [ "$FAILURES" -gt 0 ]; then
        echo "${BOLD}${YELLOW}Finished with $FAILURES issue(s) above.${RESET}"
    else
        echo "${BOLD}${GREEN}Ready.${RESET}"
    fi
    echo
    echo "Open it:    pick 'Connect WiFi' on the HAT desktop (hold the button), or ./run.sh"
    echo "Controls:   press = next · hold 1s = select · 4 quick presses = back to desktop"
    echo "Keyboard:   arrows move · Enter selects · Esc goes back · type SSID/password"
}
