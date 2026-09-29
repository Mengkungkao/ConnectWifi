#!/usr/bin/env bash
# Copy Connect WiFi to a Raspberry Pi or Orange Pi from this machine.
#
#   ./deploy.sh jarvis@192.168.0.33
#   ./deploy.sh orangepi@192.168.0.130 --setup
#   ./deploy.sh user@host other-dir --setup
#
# --setup then runs ./setup.sh on the device over an interactive ssh
# session, so one command installs everything. It asks for the device's
# sudo password there.
set -euo pipefail

RUN_SETUP=0
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --setup) RUN_SETUP=1 ;;
        *)       POSITIONAL+=("$arg") ;;
    esac
done
TARGET="${POSITIONAL[0]:-}"
REMOTE_DIR="${POSITIONAL[1]:-ConnectWifi}"
if [ -z "$TARGET" ]; then
    echo "usage: $0 user@host [remote-dir] [--setup]" >&2
    exit 1
fi

HERE="$(dirname "$(readlink -f "$0")")"
SSH_OPTS=(-o ConnectTimeout=25)   # a Zero 2 W is slow to answer; 10s hangs

echo "==> syncing to ${TARGET}:${REMOTE_DIR}"
rsync -az --delete \
    --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude '.pytest_cache' --exclude 'screenshots' --exclude '.sugar-wifi-conf.*' \
    -e "ssh ${SSH_OPTS[*]}" \
    "${HERE}/" "${TARGET}:${REMOTE_DIR}/"
ssh "${SSH_OPTS[@]}" "$TARGET" "cd ${REMOTE_DIR} && chmod +x run.sh setup.sh register.sh install-*.sh uninstall.sh"

if [ "$RUN_SETUP" = 1 ]; then
    echo
    echo "==> running setup on ${TARGET}"
    # -t: setup asks before changing anything, and sudo wants a password.
    exec ssh -t "${SSH_OPTS[@]}" "$TARGET" "cd ${REMOTE_DIR} && ./setup.sh"
fi

echo
echo "==> deployed. On the device:"
echo "    cd ${REMOTE_DIR} && ./setup.sh"
