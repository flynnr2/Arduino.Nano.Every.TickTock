#!/usr/bin/env bash
# Optional host diagnostics only; does not restart acquisition or reboot.
set -Eeuo pipefail
DIAGNOSTICS_SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python3 - "$DIAGNOSTICS_SOURCE/reboot_health.py" <<'PY'
import ast, pathlib, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text())
PY
if [[ ${1:-} == --check ]]; then
    bash -n "$DIAGNOSTICS_SOURCE/install-diagnostics.sh"
    echo 'Diagnostic installer inputs are valid; no changes made.'
    exit 0
fi
[[ $# == 0 ]] || { echo 'Usage: sudo bash install-diagnostics.sh [--check]' >&2; exit 2; }
[[ $EUID == 0 && $(uname -s) == Linux ]] || { echo 'Run with sudo on the Pi.' >&2; exit 1; }
grep -q 'Raspberry Pi Zero 2 W' /proc/device-tree/model || { echo 'This crash-buffer configuration is for Raspberry Pi Zero 2 W only.' >&2; exit 1; }
BOOT_CONFIG=/boot/firmware/config.txt
[[ -f $BOOT_CONFIG && -f /boot/firmware/overlays/ramoops.dtbo ]] || { echo 'Pi boot configuration or ramoops overlay missing.' >&2; exit 1; }
# Refuse ambiguous existing crash-capture settings rather than overwrite them.
python3 - "$BOOT_CONFIG" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
expected = 'dtoverlay=ramoops,total-size=0x20000,record-size=0x8000,console-size=0x10000'
active = re.findall(r'^\s*(dtoverlay\s*=\s*ramoops[^\n]*)', text, re.M)
if active and active != [expected]:
    raise SystemExit('Existing ramoops configuration differs; review it before installation.')
PY
install -d -m 0755 /usr/local/lib/pendulum-diagnostics /etc/systemd/journald.conf.d
install -m 0644 "$DIAGNOSTICS_SOURCE/reboot_health.py" /usr/local/lib/pendulum-diagnostics/reboot_health.py
install -m 0644 "$DIAGNOSTICS_SOURCE/pendulum-health.service" /etc/systemd/system/pendulum-health.service
install -m 0644 "$DIAGNOSTICS_SOURCE/90-pendulum-diagnostics.conf" /etc/systemd/journald.conf.d/90-pendulum-diagnostics.conf
systemd-analyze verify /etc/systemd/system/pendulum-health.service
python3 - "$BOOT_CONFIG" <<'PY'
import os, pathlib, shutil, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
line = 'dtoverlay=ramoops,total-size=0x20000,record-size=0x8000,console-size=0x10000'
if line not in text.splitlines():
    backup = path.with_name('config.txt.before-pendulum-diagnostics')
    if not backup.exists():
        shutil.copy2(path, backup)
    with path.open('a') as stream:
        stream.write('\n[all]\n# Preserve kernel crash and console evidence across warm resets.\n' + line + '\n')
        stream.flush()
        os.fsync(stream.fileno())
PY
systemctl daemon-reload
systemctl restart systemd-journald
journalctl --flush
journalctl --sync
systemctl enable --now pendulum-health.service
sleep 2
systemctl is-active pendulum-health.service
test -s /var/log/pendulum-health/last-state.json
test -n "$(find /var/log/journal -name '*.journal' -print -quit)"
echo 'Persistent journal and health recording are active. Crash capture activates at the next boot.'
echo 'No acquisition services were restarted and no reboot was requested.'
