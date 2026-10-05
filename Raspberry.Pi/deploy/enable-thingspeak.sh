#!/usr/bin/env bash
# Explicit commissioning: validate the write key remotely before enabling uploads.
set -Eeuo pipefail

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<'EOF'
Usage: sudo bash Raspberry.Pi/deploy/enable-thingspeak.sh

Validate the configured write key with one ThingSpeak status-only test entry,
then enable the optional hourly timer. A failed validation leaves it disabled.
Configure /var/lib/pendulum/thingspeak.json and its private key file first.
This command never starts or stops clock acquisition.
EOF
    exit 0
fi
if [[ $# -ne 0 || $EUID -ne 0 || $(uname -s) != Linux ]]; then
    echo 'Run without arguments using sudo on the Pi; use --help for details.' >&2
    exit 1
fi
for command in systemctl timeout runuser; do
    command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/thingspeak-setup.sh"
thingspeak_validate_and_enable
