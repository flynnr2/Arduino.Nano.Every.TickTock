"""Selective deployment must never stop acquisition for dashboard-only changes."""
import importlib.util
from pathlib import Path
import shutil
import pytest

path=Path(__file__).resolve().parents[2]/'Raspberry.Pi/deploy/dashboard_update_plan.py'
spec=importlib.util.spec_from_file_location('dashboard_update_plan',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.fixture
def packages(tmp_path):
    source=tmp_path/'source';installed=tmp_path/'installed'
    (source/'Raspberry.Pi/pendulum_pi/static').mkdir(parents=True)
    (source/'pendulum_analysis').mkdir()
    (source/'Raspberry.Pi/pendulum_pi/web.py').write_text('x=1\n')
    (source/'Raspberry.Pi/pendulum_pi/history.py').write_text('def writer(): return 1\ndef query_history(): return 1\n')
    (source/'pendulum_analysis/__init__.py').write_text('')
    shutil.copytree(source/'Raspberry.Pi/pendulum_pi',installed/'pi')
    shutil.copytree(source/'pendulum_analysis',installed/'analysis')
    return source,installed/'pi',installed/'analysis'


def test_unchanged_install_restarts_nothing(packages):
    assert module.plan(*packages)['restart_services']==[]


def test_only_affected_services_are_selected(packages):
    root,pi,analysis=packages
    (root/'Raspberry.Pi/pendulum_pi/web.py').write_text('x=2\n')
    assert module.plan(*packages)['restart_services']==['pendulum-web']
    (root/'pendulum_analysis/air_density.py').write_text('x=2\n')
    assert module.plan(*packages)['restart_services']==['pendulum-views','pendulum-web']
    (root/'Raspberry.Pi/pendulum_pi/static/app.js').write_text('new')
    assert module.plan(*packages)['restart_services']==['pendulum-views','pendulum-web']


def test_history_query_change_preserves_logger_but_writer_change_rejected(packages):
    root,_,_=packages;p=root/'Raspberry.Pi/pendulum_pi/history.py'
    p.write_text('from pendulum_analysis.air_density import density_values\ndef writer(): return 1\ndef query_history(): return 2\n')
    assert module.plan(*packages)['restart_services']==['pendulum-views','pendulum-web']
    p.write_text('def writer(): return 2\ndef query_history(): return 2\n')
    with pytest.raises(ValueError,match='outside this dashboard-only'):module.plan(*packages)


def test_capture_changes_fail_before_touching_services(packages):
    root,_,_=packages;(root/'Raspberry.Pi/pendulum_pi/service.py').write_text('changed')
    with pytest.raises(ValueError,match='no services stopped'):module.plan(*packages)
