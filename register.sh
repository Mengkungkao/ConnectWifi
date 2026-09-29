#!/usr/bin/env bash
# Put Connect WiFi on the Whisplay HAT desktop. A plain call to
# whisplay-daemon's socket: no sudo needed. The installers run this;
# run it yourself after moving this folder.
#
#   ./register.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

python3 - <<'REGISTER'
import sys

sys.path.insert(0, ".")
from connectwifi.board import registration
from connectwifi.daemon_client import DaemonError, request

entry = registration()
try:
    request("app.register", entry)
except (OSError, DaemonError) as exc:
    sys.exit(f"whisplay-daemon is not answering ({exc}); is it running?")
print(f"registered '{entry['display_name']}' ({entry['app_id']}): {entry['launch_command']}")
REGISTER
