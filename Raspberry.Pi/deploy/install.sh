#!/usr/bin/env bash
# Install on the Pi, from any checkout location. Never changes boot/GPIO settings.
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
source "$SCRIPT_DIR/thingspeak-setup.sh"
SERVICES=(pendulum-i2c-recovery pendulum-acquire pendulum-analyze pendulum-views pendulum-sensors pendulum-oled pendulum-web pendulum-storage)
THINGSPEAK_UNITS=(pendulum-thingspeak.timer pendulum-thingspeak.service)

usage() {
    cat <<'EOF'
Usage: sudo bash Raspberry.Pi/deploy/install.sh [--revision COMMIT_OR_TAG]
       bash Raspberry.Pi/deploy/install.sh --check
       bash Raspberry.Pi/deploy/install.sh --help

Install/update the local application on Raspberry Pi OS Lite (Python >=3.11).
Creates /opt/pendulum/venv, /var/lib/pendulum/config.json and systemd units.
Existing configuration and recordings are preserved. Services stop during update.
Seven application services plus an I2C recovery helper start automatically; ThingSpeak is optional and initially off.
An enabled ThingSpeak timer resumes only with a previously validated current key.
Initial web binding is 0.0.0.0:8080 (trusted LAN); no boot configuration is edited.
--check checks installer inputs without installing, changing files, or using sudo.
Git installations require a clean checkout; --revision also requires matching HEAD.
EOF
}

check_inputs() {
    bash -n "$SCRIPT_DIR/install.sh"
    bash -n "$SCRIPT_DIR/enable-thingspeak.sh"
    bash -n "$SCRIPT_DIR/thingspeak-setup.sh"
    python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$SCRIPT_DIR/source_revision.py"
    python3 - "$SOURCE_DIR" <<'PY'
import ast
from configparser import ConfigParser
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11 or newer is required')
root = Path(sys.argv[1])
for name in ('pyproject.toml', 'pendulum_pi/__main__.py', 'deploy/pendulum.conf'):
    if not (root / name).is_file():
        raise SystemExit(f'Missing installation input: {name}')
ast.parse((root / 'pendulum_pi/__main__.py').read_text())
for name in ('pyproject.toml', 'README.md', 'pendulum_analysis/suite/swings.py'):
    if not (root.parent / name).is_file():
        raise SystemExit(f'Missing swing-analysis installation input: {name}; use the complete repository')
for action in ('acquire', 'analyze', 'views', 'sensors', 'oled', 'web', 'storage'):
    unit = root / 'deploy' / f'pendulum-{action}.service'
    config = ConfigParser(interpolation=None, strict=False)
    config.read(unit)
    expected = f'/opt/pendulum/venv/bin/python -m pendulum_pi {action} --config /var/lib/pendulum/config.json'
    if config.get('Service', 'ExecStart', fallback='') != expected:
        raise SystemExit(f'Invalid ExecStart: {unit.name}')
recovery = ConfigParser(interpolation=None, strict=False)
recovery.read(root / 'deploy/pendulum-i2c-recovery.service')
if recovery.get('Service', 'ExecStart', fallback='') != '/opt/pendulum/venv/bin/python -I -m pendulum_pi.i2c_recovery':
    raise SystemExit('Invalid recovery helper command')
ast.parse((root / 'pendulum_pi/i2c_recovery.py').read_text())
publisher = ConfigParser(interpolation=None, strict=False)
publisher.read(root / 'deploy/pendulum-thingspeak.service')
expected = ('/opt/pendulum/venv/bin/python -m pendulum_pi thingspeak '
            '--config /var/lib/pendulum/config.json '
            '--publisher-config /var/lib/pendulum/thingspeak.json')
required = {'Type': 'oneshot', 'ExecStart': expected,
            'ExecCondition': expected + ' --check-ready',
            'TimeoutStartSec': '20', 'Restart': 'no'}
for key, value in required.items():
    if publisher.get('Service', key, fallback='') != value:
        raise SystemExit(f'Invalid publisher service {key}')
timer = ConfigParser(interpolation=None, strict=False)
timer.read(root / 'deploy/pendulum-thingspeak.timer')
for key, value in {'OnActiveSec': '1h', 'OnUnitInactiveSec': '1h',
                   'Persistent': 'false', 'Unit': 'pendulum-thingspeak.service'}.items():
    if timer.get('Timer', key, fallback='') != value:
        raise SystemExit(f'Invalid publisher timer {key}')
print('Installer syntax and packaged service commands are valid. No changes made.')
PY
}

expected_revision=''
case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --check) check_inputs; exit 0 ;;
    --revision)
        [[ $# -eq 2 && -n $2 ]] || { usage >&2; exit 2; }
        expected_revision=$2
        shift 2
        ;;
    '') ;;
    *) usage >&2; exit 2 ;;
esac
if [[ $# -ne 0 ]]; then
    usage >&2
    exit 2
fi
if [[ $(uname -s) != Linux ]]; then
    echo 'Install only on the Raspberry Pi running Linux; use --check elsewhere.' >&2
    exit 1
fi
if [[ $EUID -ne 0 ]]; then
    echo 'Run this installer with sudo on the Pi.' >&2
    exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
    echo 'A running systemd installation is required.' >&2
    exit 1
fi
for command in apt-get systemctl systemd-tmpfiles python3 runuser; do
    command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done
check_inputs
# Reject ambiguous Git sources before apt or service changes. Copy-based installs
# remain supported, but cannot claim a Git revision or use --revision.
source_revision=$(python3 "$SCRIPT_DIR/source_revision.py" "$SOURCE_DIR/.." "$expected_revision")
umask 077
staging=''
services_stopped=0
thingspeak_was_enabled=0
cleanup() {
    local result=$?
    if [[ -n $staging && -d $staging ]]; then
        rm -rf -- "$staging"
    fi
    if [[ $result -ne 0 && $services_stopped -eq 1 ]]; then
        # A failed start may have started some units; leave one consistent state.
        systemctl stop "${SERVICES[@]}" >/dev/null 2>&1 || true
        systemctl stop "${THINGSPEAK_UNITS[@]}" >/dev/null 2>&1 || true
        echo 'Installation failed after services were stopped. Configuration and recordings remain in /var/lib/pendulum.' >&2
        echo 'Resolve the error and rerun this installer; services have not been deliberately restarted with a partial update.' >&2
    fi
    exit "$result"
}
trap cleanup EXIT

# Install build prerequisites before interrupting an existing capture.
apt-get update
# piwheels NumPy links to the system OpenBLAS runtime; pip cannot install it.
apt-get install -y python3-venv python3-dev build-essential i2c-tools libjpeg-dev zlib1g-dev libopenblas0-pthread

for group in pendulum dialout i2c; do
    if ! getent group "$group" >/dev/null; then
        groupadd --system "$group"
    fi
done
if ! id pendulum >/dev/null 2>&1; then
    useradd --system --gid pendulum --home-dir /var/lib/pendulum --no-create-home --shell /usr/sbin/nologin pendulum
fi
usermod --append --groups dialout,i2c pendulum

for directory in /opt/pendulum /opt/pendulum/venv /var/lib/pendulum /var/lib/pendulum/data /run/pendulum /run/pendulum-i2c /run/pendulum/thingspeak /var/lib/pendulum/thingspeak; do
    if [[ -L $directory ]]; then
        echo "Refusing symlink at managed installation path: $directory" >&2
        exit 1
    fi
done
install -d -m 0755 -o root -g root /opt/pendulum
install -d -m 0750 -o pendulum -g pendulum /var/lib/pendulum /var/lib/pendulum/data
install -m 0644 -o root -g root "$SCRIPT_DIR/pendulum.conf" /etc/tmpfiles.d/pendulum.conf
systemd-tmpfiles --create /etc/tmpfiles.d/pendulum.conf

# Missing units on first install are normal. Only stop units systemd knows about.
services_stopped=1
thingspeak_stop_for_upgrade
for service in "${SERVICES[@]}"; do
    if [[ $(systemctl show "$service.service" --property=LoadState --value) != not-found ]]; then
        systemctl stop "$service.service"
    fi
done

# Build from a private copy; installed services never depend on the checkout.
staging=$(mktemp -d /tmp/pendulum-install.XXXXXXXX)
cp "$SOURCE_DIR/pyproject.toml" "$staging/pyproject.toml"
cp -R "$SOURCE_DIR/pendulum_pi" "$staging/pendulum_pi"
mkdir "$staging/analysis"
cp "$SOURCE_DIR/../pyproject.toml" "$SOURCE_DIR/../README.md" "$staging/analysis/"
cp -R "$SOURCE_DIR/../pendulum_analysis" "$staging/analysis/pendulum_analysis"
# Catch checkout changes during staging instead of recording a misleading HEAD.
[[ $(python3 "$SCRIPT_DIR/source_revision.py" "$SOURCE_DIR/.." "$expected_revision") == "$source_revision" ]] || {
    echo 'Source revision changed during installation.' >&2
    exit 1
}
python3 -m venv /opt/pendulum/venv
/opt/pendulum/venv/bin/python -m pip install --upgrade pip
/opt/pendulum/venv/bin/python -m pip install --upgrade "$staging[hardware]"
/opt/pendulum/venv/bin/python -m pip install --upgrade "$staging/analysis"
/opt/pendulum/venv/bin/python -m pip freeze > /opt/pendulum/requirements-installed.txt
# Freeze records versions for later diagnosis, without an obsolete staging URL.
/opt/pendulum/venv/bin/python - <<'PY'
from importlib.metadata import version
from pathlib import Path
path = Path('/opt/pendulum/requirements-installed.txt')
lines = path.read_text().splitlines()
for name in ('pendulum-pi', 'pendulum-analysis'):
    lines = [name + '==' + version(name) if line.startswith(name + ' @ ') else line for line in lines]
path.write_text('\n'.join(lines) + '\n')
PY
printf '%s\n' "$source_revision" > /opt/pendulum/source-revision.txt
chmod -R a+rX /opt/pendulum/venv
chown -R root:root /opt/pendulum/venv

# Check the installed packages as the service account, without checkout imports.
runuser -u pendulum -- /opt/pendulum/venv/bin/python -I -c \
    'from pendulum_pi.phase import PhaseSnapshots; from pendulum_analysis.suite.swings import analyze_swings; from pendulum_analysis.pps.timescale import build_timescale'

if [[ -L /var/lib/pendulum/config.json ]]; then
    echo 'Refusing symlink at /var/lib/pendulum/config.json' >&2
    exit 1
fi
if [[ ! -e /var/lib/pendulum/config.json ]]; then
    /opt/pendulum/venv/bin/python - <<'PY'
from dataclasses import replace
from pathlib import Path
import secrets
from pendulum_pi.config import Settings, save_settings
path = Path('/var/lib/pendulum/config.json')
save_settings(path, replace(Settings(), data_dir=Path('/var/lib/pendulum/data'),
                           runtime_dir=Path('/run/pendulum'), web_host='0.0.0.0',
                           api_token=secrets.token_urlsafe(32)))
path.chmod(0o600)
PY
fi
chown pendulum:pendulum /var/lib/pendulum/config.json
chmod 0600 /var/lib/pendulum/config.json
runuser -u pendulum -- /opt/pendulum/venv/bin/python -m pendulum_pi check-config --config /var/lib/pendulum/config.json >/dev/null

for service in "${SERVICES[@]}"; do
    install -m 0644 -o root -g root "$SCRIPT_DIR/$service.service" "/etc/systemd/system/$service.service"
done
for unit in "${THINGSPEAK_UNITS[@]}"; do
    install -m 0644 -o root -g root "$SCRIPT_DIR/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable "${SERVICES[@]}"
systemctl start "${SERVICES[@]}"
services_stopped=0
# This local readiness check does not contact ThingSpeak or create test entries.
# An optional publisher problem must never take the seven local services down.
thingspeak_resume_after_upgrade
printf '%s\n' 'Installed and started seven Pendulum services.' \
    'Configuration: /var/lib/pendulum/config.json (administrator token is not printed).' \
    'Initial LAN dashboard: http://<pi-hostname>:8080/' \
    'Follow Raspberry.Pi/WIRING.md and HARDWARE_TESTS.md to verify the physical setup.'
