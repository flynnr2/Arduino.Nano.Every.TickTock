#!/usr/bin/env bash
# Update an existing installation from merged Git source without stopping capture.
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
usage() {
    echo 'Usage: sudo bash Raspberry.Pi/deploy/update-dashboard.sh --revision COMMIT_OR_TAG [--plan]'
    echo 'Compares installed packages; restarts only changed dashboard/view services. Capture, timing analysis, sensors, OLED and storage stay running.'
}
revision='';plan_only=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --revision) [[ $# -ge 2 ]] || { usage; exit 2; };revision=$2;shift 2 ;;
        --plan) plan_only=1;shift ;;
        --help|-h) usage;exit 0 ;;
        *) usage;exit 2 ;;
    esac
done
[[ -n $revision ]] || { usage;exit 2; }
[[ $(uname -s) == Linux && $EUID == 0 ]] || { echo 'Run with sudo on the Pi.' >&2;exit 1; }
VENV=/opt/pendulum/venv
[[ -x $VENV/bin/python ]] || { echo 'Full installation required.' >&2;exit 1; }
source_revision=$(python3 "$SCRIPT_DIR/source_revision.py" "$REPO_DIR" "$revision")
staging=$(mktemp -d /tmp/pendulum-dashboard.XXXXXXXX)
trap 'rm -rf -- "$staging"' EXIT
installed_pi=$("$VENV/bin/python" -I -c 'import importlib.util; print(next(iter(importlib.util.find_spec("pendulum_pi").submodule_search_locations)))')
installed_analysis=$("$VENV/bin/python" -I -c 'import importlib.util; print(next(iter(importlib.util.find_spec("pendulum_analysis").submodule_search_locations)))')
python3 "$SCRIPT_DIR/dashboard_update_plan.py" "$REPO_DIR" "$installed_pi" "$installed_analysis" > "$staging/plan.json"
cat "$staging/plan.json"
((plan_only == 0)) || exit 0
if ! python3 - "$staging/plan.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]));sys.exit(0 if any(p['changes'].values()) else 1)
PY
then
    echo 'Installed package contents already match. No services restarted.'
    exit 0
fi
# Build before interrupting the web/view workers; dependencies are unchanged.
"$VENV/bin/python" -m pip wheel --no-deps --wheel-dir "$staging/wheels" "$REPO_DIR" "$REPO_DIR/Raspberry.Pi"
[[ $(python3 "$SCRIPT_DIR/source_revision.py" "$REPO_DIR" "$revision") == "$source_revision" ]] || exit 1
services=()
while IFS= read -r unit; do [[ -z $unit ]] || services+=("$unit"); done < <(python3 - "$staging/plan.json" <<'PY'
import json,sys
print('\n'.join(json.load(open(sys.argv[1]))['restart_services']))
PY
)
if ((${#services[@]})); then systemctl stop "${services[@]}"; fi
# A failed install leaves only the affected dashboard/view workers stopped.
# The logger and timing analysis are never stopped or restarted by this script.
"$VENV/bin/python" -m pip install --no-deps --force-reinstall "$staging"/wheels/*.whl
"$VENV/bin/python" -m pip check
chmod -R a+rX "$VENV"
python3 "$SCRIPT_DIR/dashboard_update_plan.py" "$REPO_DIR" "$installed_pi" "$installed_analysis" > "$staging/verified.json"
python3 - "$staging/verified.json" <<'PY'
import json,sys
if any(json.load(open(sys.argv[1]))['changes'].values()):
    raise SystemExit('Installed package content verification failed; affected workers remain stopped')
PY
printf '%s\n' "$source_revision" > /opt/pendulum/dashboard-source-revision.txt
chmod 0644 /opt/pendulum/dashboard-source-revision.txt
if ((${#services[@]})); then systemctl start "${services[@]}";systemctl is-active "${services[@]}"; fi
echo 'Dashboard package contents verified; capture and timing services were left running.'
