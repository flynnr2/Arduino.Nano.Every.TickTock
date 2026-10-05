"""Runtime examples are exempt only as prose, and ignored files stay out of audits."""
from pathlib import Path
import subprocess

from scripts import doc_audit


def test_runtime_examples_do_not_claim_checkout_files(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    doc = Path('guide.md')
    (tmp_path / doc).write_text('`/run/pendulum-i2c/status.json` and `runtime_dir/status.json`\n')
    assert doc_audit.check_missing_referenced_files([doc]) == []
    (tmp_path / doc).write_text('[broken link](/run/pendulum-i2c/not-a-real-file.json)\n')
    assert doc_audit.check_markdown_links([doc])


def test_unknown_source_path_is_still_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    (tmp_path / 'guide.md').write_text('Read `missing/source.py`.\n')
    assert doc_audit.check_missing_referenced_files([Path('guide.md')])


def test_inventory_includes_new_docs_but_not_ignored_files(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / '.gitignore').write_text('.DS_Store\n')
    (tmp_path / '.DS_Store').write_text('ignored')
    (tmp_path / 'new-guide.md').write_text('# New guide\n')
    inventory = doc_audit.tracked_files()
    assert Path('new-guide.md') in inventory
    assert Path('.DS_Store') not in inventory
