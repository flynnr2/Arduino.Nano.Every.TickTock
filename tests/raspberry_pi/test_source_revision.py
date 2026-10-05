"""Installation provenance rejects ambiguity before touching the running Pi."""
import importlib.util
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'Raspberry.Pi/deploy/source_revision.py'
spec = importlib.util.spec_from_file_location('source_revision', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def checkout(tmp_path):
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    (tmp_path / 'source.py').write_text('original\n')
    (tmp_path / '.gitignore').write_text('data/\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'Baseline')
    git(tmp_path, 'tag', 'known-good')
    return tmp_path


def test_clean_revision_and_tag(checkout):
    assert module.source_revision(checkout, 'known-good') == git(checkout, 'rev-parse', 'HEAD')
    (checkout / 'data').mkdir()
    (checkout / 'data/recording.csv').write_text('ignored local recording')
    assert module.source_revision(checkout) == git(checkout, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('change', ['modified', 'staged', 'untracked', 'deleted'])
def test_dirty_checkout_rejected(checkout, change):
    if change == 'deleted':
        (checkout / 'source.py').unlink()
    elif change == 'untracked':
        (checkout / 'extra.py').write_text('new')
    else:
        (checkout / 'source.py').write_text('changed')
        if change == 'staged':
            git(checkout, 'add', '.')
    with pytest.raises(ValueError, match='dirty checkout'):
        module.source_revision(checkout)


def test_revision_mismatch_and_invalid_ref(checkout):
    git(checkout, 'commit', '--allow-empty', '-m', 'Next')
    with pytest.raises(ValueError, match='does not match'):
        module.source_revision(checkout, 'known-good')
    with pytest.raises(ValueError, match='does not resolve'):
        module.source_revision(checkout, '--help')


def test_unversioned_source_is_honest(tmp_path):
    assert module.source_revision(tmp_path) == 'Source copied without Git revision information.'
    with pytest.raises(ValueError, match='requires a Git checkout'):
        module.source_revision(tmp_path, 'known-good')


def test_nested_checkout_is_rejected(checkout):
    nested = checkout / 'nested'
    nested.mkdir()
    with pytest.raises(ValueError, match='checkout root'):
        module.source_revision(nested)
