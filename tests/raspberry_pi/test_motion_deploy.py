"""Budget overrides are explicit, bounded and rejected before service changes."""
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'Raspberry.Pi/deploy/install.sh'


@pytest.mark.parametrize('value', ['0', '7', '4097', '10000', '-1', 'abc', '1.2', ''])
def test_invalid_history_budget_is_rejected_before_installation(value):
    result = subprocess.run(['bash', str(SCRIPT), '--history-max-mb', value], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'History budget' in result.stderr


def test_budget_can_be_validated_without_installing():
    result = subprocess.run(['bash', str(SCRIPT), '--history-max-mb', '2048', '--check'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'No changes made' in result.stdout
