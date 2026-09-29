#!/usr/bin/env bash
# Launch Connect WiFi. whisplay-daemon runs this when the app is picked
# from the HAT desktop, so it must work from any working directory.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

export PYTHONUNBUFFERED=1
exec python3 -m connectwifi "$@"
