"""Recovery privileges are confined to the helper and its fixed runtime files."""
from configparser import ConfigParser
from pathlib import Path
import subprocess

DEPLOY = Path(__file__).resolve().parents[2] / 'Raspberry.Pi/deploy'


def test_recovery_service_and_worker_coordination_permissions():
    unit = ConfigParser(interpolation=None, strict=False)
    unit.read(DEPLOY / 'pendulum-i2c-recovery.service')
    service = unit['Service']
    assert service['User'] == 'root' and service['Group'] == 'pendulum'
    assert service['ExecStart'] == '/opt/pendulum/venv/bin/python -I -m pendulum_pi.i2c_recovery'
    assert service['WorkingDirectory'] == '/opt/pendulum'
    assert service['WatchdogSec'] == '30' and service['NoNewPrivileges'] == 'true'
    assert set(service['ReadWritePaths'].split()) == {
        '/run/pendulum-i2c', '/sys/bus/platform/drivers/i2c-bcm2835'}
    for name in ('sensors', 'oled'):
        worker = ConfigParser(interpolation=None, strict=False)
        worker.read(DEPLOY / f'pendulum-{name}.service')
        assert worker['Service']['User'] == 'pendulum'
        assert 'pendulum-i2c-recovery.service' in worker['Unit']['After']
        assert '/run/pendulum-i2c' in worker['Service']['ReadWritePaths'].split()
    tmpfiles = (DEPLOY / 'pendulum.conf').read_text()
    assert 'd /run/pendulum-i2c 0750 root pendulum -' in tmpfiles
    assert 'f /run/pendulum-i2c/bus-1.lock 0660 root pendulum -' in tmpfiles


def test_installer_checks_and_installs_recovery_unit_with_app_services():
    result = subprocess.run(['bash', str(DEPLOY / 'install.sh'), '--check'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    source = (DEPLOY / 'install.sh').read_text()
    assert 'SERVICES=(pendulum-i2c-recovery ' in source
    assert 'systemctl stop "$service.service"' in source
    assert 'systemctl enable "${SERVICES[@]}"' in source
