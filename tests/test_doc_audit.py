"""Runtime examples are exempt only as prose, and ignored files stay out of audits."""
from pathlib import Path
import subprocess

import pytest

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


def test_inline_build_commands_check_script_not_generated_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    (tmp_path/'tools').mkdir()
    (tmp_path/'tools/build.py').write_text('# build\n')
    (tmp_path/'guide.md').write_text('Run `python3 tools/build.py board.kicad_pcb\n   output/routes.json`.\n')
    assert doc_audit.check_missing_referenced_files([Path('guide.md')]) == []
    (tmp_path/'guide.md').write_text('Run `python3 missing/build.py output/routes.json`.\n')
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


def test_carrier_export_rules_keep_release_files_but_exclude_desktop_metadata(tmp_path, monkeypatch):
    rules = (Path(__file__).resolve().parents[1] / 'Hardware/NanoEveryCarrierA9/.gitignore').read_text()
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / '.gitignore').write_text('output/\n.DS_Store\n__MACOSX/\n')
    carrier = tmp_path / 'Hardware/NanoEveryCarrierA9'
    output = carrier / 'output'
    output.mkdir(parents=True)
    (carrier / '.gitignore').write_text(rules)
    (output / 'release.zip').write_text('release placeholder')
    (output / '.DS_Store').write_text('desktop metadata')
    (output / '__MACOSX').mkdir()
    (output / '__MACOSX/metadata.txt').write_text('desktop metadata')
    inventory = doc_audit.tracked_files()
    assert Path('Hardware/NanoEveryCarrierA9/output/release.zip') in inventory
    assert not any(path.name == '.DS_Store' or '__MACOSX' in path.parts for path in inventory)


@pytest.mark.parametrize('names', [
    [
        'Hardware/NanoEveryCarrier/validation/connectivity.txt',
        'Hardware/NanoEveryCarrier/WiredSchematic/validation/connectivity.txt',
    ],
    [
        'Hardware/NanoEveryCarrier/LICENSE-KiCad-libraries.txt',
        'Hardware/NanoEveryCarrierA9/LICENSE-KiCad-libraries.txt',
    ],
    [
        'Hardware/NanoEveryCarrierA9/WiredReadable/validation/original-wired/equivalence.txt',
        'Hardware/NanoEveryCarrierA9/WiredSchematic/validation/equivalence.txt',
    ],
])
def test_matching_frozen_carrier_files_are_expected(tmp_path, monkeypatch, names):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    reports = [Path(name) for name in names]
    for report in reports:
        (tmp_path / report).parent.mkdir(parents=True)
        (tmp_path / report).write_text('PASS: Same electrical connectivity\n')

    assert doc_audit.check_duplicate_files(reports) == []

    extra = Path('unexpected-copy.txt')
    (tmp_path / extra).write_text('PASS: Same electrical connectivity\n')
    issues = doc_audit.check_duplicate_files(reports + [extra])
    assert len(issues) == 1
    assert all(str(path) in issues[0] for path in reports + [extra])


def test_other_duplicate_reports_are_still_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    reports = [Path('first.txt'), Path('second.txt')]
    for report in reports:
        (tmp_path / report).write_text('Unexpected duplicate report\n')

    issues = doc_audit.check_duplicate_files(reports)
    assert len(issues) == 1
    assert all(str(path) in issues[0] for path in reports)


@pytest.mark.parametrize('command', [
    'python3 tools/build.py input.json output/new.json',
    'python3 tools/build.py input.json\n  output/new.json',
    'tools/build.py input.json output/new.json',
    'python3 -I "tools/build with spaces.py" output/new.json',
])
def test_inline_commands_check_the_script_not_generated_arguments(tmp_path, monkeypatch, command):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    (tmp_path / 'tools').mkdir()
    for name in ['build.py', 'build with spaces.py']:
        (tmp_path / 'tools' / name).write_text('# builder\n')
    guide = tmp_path / 'guide.md'
    guide.write_text('Run `' + command + '`.\n')
    assert doc_audit.check_missing_referenced_files([Path('guide.md')]) == []


def test_missing_script_and_standalone_input_are_still_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_audit, 'ROOT', tmp_path)
    (tmp_path / 'guide.md').write_text(
        'Run `python3 missing/build.py output/new.json`; read `validation/missing.json`.\n')
    issues = doc_audit.check_missing_referenced_files([Path('guide.md')])
    assert len(issues) == 2
    assert any('missing/build.py' in issue for issue in issues)
    assert any('validation/missing.json' in issue for issue in issues)
