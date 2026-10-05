"""Exercise optional systemd activation without sudo, a Pi or cloud writes."""
from configparser import ConfigParser
import os
from pathlib import Path
import subprocess

import pytest


DEPLOY = Path(__file__).resolve().parents[2] / 'Raspberry.Pi' / 'deploy'


def unit(name):
    config = ConfigParser(interpolation=None, strict=False)
    assert config.read(DEPLOY / name)
    return config


def run_lifecycle(tmp_path, action, *, enabled='disabled', loaded='loaded',
                  ready=0, validation=0, start=0):
    """Run the production lifecycle with local shell substitutes for side effects."""
    command_log = tmp_path / 'commands.log'
    script = r'''
set -Eeuo pipefail
source "$SETUP_SCRIPT"
systemctl() {
    printf 'systemctl %s\n' "$*" >> "$COMMAND_LOG"
    case "$1" in
        is-enabled) printf '%s\n' "$TIMER_ENABLED" ;;
        show) printf '%s\n' "$UNIT_LOADED" ;;
        start) return "$START_RESULT" ;;
    esac
}
runuser() {
    printf 'runuser %s\n' "$*" >> "$COMMAND_LOG"
    case "${!#}" in
        --validate-key) return "$VALIDATION_RESULT" ;;
        --check-ready) return "$READY_RESULT" ;;
        *) return 99 ;;
    esac
}
timeout() {
    printf 'timeout %s\n' "$*" >> "$COMMAND_LOG"
    shift 2
    "$@"
}
case "$ACTION" in
    upgrade) thingspeak_stop_for_upgrade; thingspeak_resume_after_upgrade ;;
    enable) thingspeak_validate_and_enable ;;
esac
'''
    env = dict(os.environ, SETUP_SCRIPT=str(DEPLOY / 'thingspeak-setup.sh'),
               COMMAND_LOG=str(command_log), TIMER_ENABLED=enabled,
               UNIT_LOADED=loaded, READY_RESULT=str(ready),
               VALIDATION_RESULT=str(validation), START_RESULT=str(start), ACTION=action)
    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
    return result, command_log.read_text().splitlines()


def test_installer_check_remains_read_only():
    result = subprocess.run(['bash', str(DEPLOY / 'install.sh'), '--check'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'No changes made.' in result.stdout


def test_publisher_service_is_bounded_and_isolated():
    config = unit('pendulum-thingspeak.service')
    service = config['Service']
    assert service['Type'] == 'oneshot'
    assert service['TimeoutStartSec'] == '20'
    assert service['Restart'] == 'no'
    assert service['User'] == 'pendulum'
    assert service['Nice'] == '19'
    assert service['IOSchedulingClass'] == 'idle'
    assert service['ProtectSystem'] == 'strict'
    assert service['PrivateDevices'] == 'true'
    assert service['ExecCondition'] == service['ExecStart'] + ' --check-ready'
    assert set(service['ReadWritePaths'].split()) == {
        '/run/pendulum/thingspeak', '/var/lib/pendulum/thingspeak'}
    assert 'Install' not in config
    assert 'network-online' not in (DEPLOY / 'pendulum-thingspeak.service').read_text()
    for existing in DEPLOY.glob('*.service'):
        if existing.name != 'pendulum-thingspeak.service':
            assert 'thingspeak' not in existing.read_text()
    assert not {'requires', 'wants', 'bindsto', 'partof'} & set(config['Unit'])


def test_timer_is_monotonic_and_does_not_catch_up():
    timer = unit('pendulum-thingspeak.timer')['Timer']
    assert timer['OnActiveSec'] == '1h'
    assert 'OnBootSec' not in timer  # enabling after an hour of uptime must not post immediately
    assert timer['OnUnitInactiveSec'] == '1h'
    assert timer['Persistent'] == 'false'
    assert 'OnCalendar' not in timer
    assert timer['Unit'] == 'pendulum-thingspeak.service'


@pytest.mark.parametrize('state,loaded', [('disabled', 'not-found'),
                                        ('disabled', 'loaded'), ('masked', 'loaded')])
def test_install_never_activates_unenabled_publisher(tmp_path, state, loaded):
    result, calls = run_lifecycle(tmp_path, 'upgrade', enabled=state, loaded=loaded)
    assert result.returncode == 0, result.stderr
    assert not any(' --check-ready' in call or ' --validate-key' in call for call in calls)
    assert not any(call.startswith(('systemctl start ', 'systemctl enable ')) for call in calls)


@pytest.mark.parametrize('state', ['enabled', 'enabled-runtime'])
def test_upgrade_resumes_only_previously_enabled_ready_timer(tmp_path, state):
    result, calls = run_lifecycle(tmp_path, 'upgrade', enabled=state)
    assert result.returncode == 0, result.stderr
    assert calls.index('systemctl stop pendulum-thingspeak.timer') < calls.index(
        'systemctl stop pendulum-thingspeak.service')
    check_index = next(i for i, call in enumerate(calls) if call.endswith(' --check-ready'))
    assert check_index < calls.index('systemctl start pendulum-thingspeak.timer')
    assert not any(' --validate-key' in call for call in calls)


def test_upgrade_disables_timer_when_key_receipt_or_config_not_ready(tmp_path):
    result, calls = run_lifecycle(tmp_path, 'upgrade', enabled='enabled', ready=1)
    assert result.returncode == 0  # optional failure cannot fail the local installation
    assert 'systemctl disable pendulum-thingspeak.timer' in calls
    assert 'systemctl start pendulum-thingspeak.timer' not in calls
    assert 'not validated' in result.stderr


def test_optional_start_failure_does_not_fail_local_installation(tmp_path):
    result, calls = run_lifecycle(tmp_path, 'upgrade', enabled='enabled', start=1)
    assert result.returncode == 0
    assert 'local services are running' in result.stderr


def test_enable_validates_then_checks_ready_before_enabling(tmp_path):
    result, calls = run_lifecycle(tmp_path, 'enable')
    assert result.returncode == 0, result.stderr
    assert calls[:2] == ['systemctl disable --now pendulum-thingspeak.timer',
                         'systemctl stop pendulum-thingspeak.service']
    assert calls[2].startswith('timeout --kill-after=2s 20s runuser ')
    validation_index = next(i for i, call in enumerate(calls)
                            if call.startswith('runuser ') and call.endswith(' --validate-key'))
    ready_index = next(i for i, call in enumerate(calls) if call.endswith(' --check-ready'))
    assert validation_index < ready_index < calls.index('systemctl enable --now pendulum-thingspeak.timer')
    assert all('pendulum-acquire' not in call for call in calls)


@pytest.mark.parametrize('validation,ready', [(1, 0), (124, 0), (0, 1)])
def test_enable_failure_leaves_timer_off(tmp_path, validation, ready):
    result, calls = run_lifecycle(tmp_path, 'enable', validation=validation, ready=ready)
    assert result.returncode != 0
    assert 'systemctl disable --now pendulum-thingspeak.timer' in calls
    assert 'systemctl enable --now pendulum-thingspeak.timer' not in calls
    assert 'remains disabled' in result.stderr
