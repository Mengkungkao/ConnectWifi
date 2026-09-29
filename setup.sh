#!/usr/bin/env bash
# Install Connect WiFi on this board: picks install-raspberrypi.sh or
# install-orangepi.sh from the device tree and passes every option on.
#
#   ./setup.sh             walk through every step, asking first
#   ./setup.sh --check     report only; change nothing
#   ./setup.sh --help      the chosen installer's options
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)
case "$MODEL" in
    *"Raspberry Pi"*) exec ./install-raspberrypi.sh "$@" ;;
    *[Oo]range*)      exec ./install-orangepi.sh "$@" ;;
esac
echo "Unrecognised board '${MODEL:-unknown}'." >&2
echo "Run ./install-raspberrypi.sh or ./install-orangepi.sh, whichever is closer." >&2
exit 1
