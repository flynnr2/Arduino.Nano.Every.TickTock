"""Read rotated recordings without pooling independent receiver sessions.

Contiguous, closed segments with identical contracts are streamed to temporary
CSV inputs on the analysis machine. No source is rewritten. Unknown gaps,
missing files and session changes split reports; the index lists exclusions.
"""
from __future__ import annotations

import csv
import gzip
import html
import json
from pathlib import Path
import re
import tempfile

from .common import fingerprint, write_json

_SEGMENT = re.compile(r'segment-[0-9]+\Z')


def _root(path):
    path = Path(path)
    return path.parent if path.name == 'catalogue.json' else path


def is_collection(path):
    path = Path(path)
    if path.is_file():
        return path.name == 'catalogue.json'
    if not path.is_dir() or (path / 'manifest.json').is_file():
        return False
    return any(_SEGMENT.fullmatch(p.name) and p.is_dir() for p in path.iterdir()) or any(
        p.is_dir() and not p.is_symlink() and any(_SEGMENT.fullmatch(q.name) for q in p.iterdir())
        for p in path.iterdir()
    )


def _source(directory, name):
    candidates = [directory / name, directory / (name + '.gz')]
    present = [p for p in candidates if p.exists()]
    if any(p.is_symlink() or not p.is_file() for p in present):
        raise ValueError(f'Unsafe recording file in {directory}: {name}')
    if len(present) > 1:
        raise ValueError(f'Both compressed and original {name} exist in {directory}; finish storage maintenance first')
    return present[0] if present else None


def _read_text(path):
    return gzip.open(path, 'rt', newline='') if path.suffix == '.gz' else path.open(newline='')


def _segments(root, required_roles=('pcps', 'pcsw')):
    candidates = []
    for child in sorted(root.iterdir()):
        if child.is_symlink() or not child.is_dir():
            continue
        if _SEGMENT.fullmatch(child.name):
            candidates.append(child)
        else:
            candidates.extend(p for p in sorted(child.iterdir())
                              if not p.is_symlink() and p.is_dir() and _SEGMENT.fullmatch(p.name))
    found, excluded = [], []
    for directory in candidates:
        path = directory / 'manifest.json'
        if not path.is_file() or path.is_symlink():
            excluded.append(dict(path=str(directory), reason='missing or unsafe manifest'))
            continue
        with path.open() as stream:
            manifest = json.load(stream)
        if not isinstance(manifest, dict):
            raise ValueError(f'Invalid recording manifest: {path}')
        if (not manifest.get('closed_utc') or manifest.get('error')
                or manifest.get('closed_reason') in {'failed', 'error', 'write_error'}):
            excluded.append(dict(path=str(directory), reason='segment is active or interrupted'))
            continue
        session, segment = manifest.get('session'), manifest.get('segment')
        if not isinstance(session, str) or not session or type(segment) is not int or segment < 1:
            raise ValueError(f'Missing recording identity: {path}')
        files = {role: _source(directory, name) for role, name in
                 [('pcps', 'PCPS.CSV'), ('pcsw', 'PCSW.CSV'), ('sts', 'STS.CSV')]}
        if any(files[role] is None for role in required_roles):
            excluded.append(dict(path=str(directory), session=session, segment=segment,
                                 reason='measurement file expired or missing; use full archive'))
            continue
        found.append(dict(path=directory, session=session, segment=segment,
                          contract=manifest.get('contract'), manifest=manifest, files=files))
    found.sort(key=lambda item: (item['session'], item['segment']))
    identities = [(s['session'], s['segment']) for s in found]
    if len(identities) != len(set(identities)):
        raise ValueError('Duplicate session/segment identities in recording collection')
    return found, excluded


def _groups(segments):
    groups = []
    for segment in segments:
        previous = groups[-1][-1] if groups else None
        if previous is None or any((segment['session'] != previous['session'],
                                    segment['segment'] != previous['segment'] + 1,
                                    segment['contract'] != previous['contract'],
                                    previous['manifest'].get('closed_reason') != 'rotation')):
            groups.append([])
        groups[-1].append(segment)
    return groups


def _combine(group, destination):
    counts, provenance = {}, []
    for role, name in [('pcps', 'PCPS.CSV'), ('pcsw', 'PCSW.CSV'), ('sts', 'STS.CSV')]:
        header = None
        row_count = 0
        with (destination / name).open('w', newline='') as output:
            writer = csv.writer(output, lineterminator='\n')
            for item in group:
                source = item['files'][role]
                if source is None:
                    continue
                with _read_text(source) as stream:
                    reader = csv.reader(stream, strict=True)
                    incoming = next(reader, None)
                    if incoming is None:
                        raise ValueError(f'Empty recording file: {source}')
                    if header is None:
                        header = incoming
                        writer.writerow(header)
                    elif incoming != header:
                        raise ValueError(f'CSV header changed inside a contiguous recording: {source}')
                    first_row = row_count + 2
                    for row in reader:
                        if len(row) != len(header):
                            raise ValueError(f'Malformed recording row in {source}')
                        writer.writerow(row)
                        row_count += 1
                    provenance.append(dict(role=role, session=item['session'], segment=item['segment'],
                                           combined_first_row=first_row, rows=row_count + 2 - first_row,
                                           **fingerprint(source)))
        if header is None:
            (destination / name).unlink()
        counts[role] = row_count
    return counts, provenance


def run_collection(input_path, out, cfg, export_intervals, progress, run_single,
                   *, required_roles=('pcps', 'pcsw')):
    root, out = _root(input_path).resolve(), Path(out).resolve()
    if out == root or root.is_relative_to(out):
        raise ValueError('Output must be a separate analysis directory')
    segments, excluded = _segments(root, required_roles)
    if not segments:
        raise ValueError('No completed segments with retained measurements; select the archive or close recording first')
    if any(out == s['path'] or out.is_relative_to(s['path']) for s in segments):
        raise ValueError('Output cannot be inside a recording segment')
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, group in enumerate(_groups(segments), 1):
        name = f'part-{index:04d}'
        progress(f"Reading session {group[0]['session']}, segments {group[0]['segment']}–{group[-1]['segment']} …")
        with tempfile.TemporaryDirectory(prefix='.pendulum-input-', dir=out) as temporary:
            stage = Path(temporary)
            counts, provenance = _combine(group, stage)
            if not counts['pcps']:
                excluded.extend(dict(path=str(s['path']), reason='metadata-only segment group') for s in group)
                continue
            if not counts['pcsw']:
                (stage / 'PCSW.CSV').unlink(missing_ok=True)
            result = run_single(stage, out / name, cfg, export_intervals, progress)
            # Temporary assembled inputs no longer exist after this scope. Preserve
            # their hashes as derived input provenance and add original source rows.
            result['provenance']['recording_segments'] = [dict(path=str(s['path']), manifest=s['manifest']) for s in group]
            result['provenance']['source_files'] = provenance
            result['provenance']['input_assembly'] = 'Contiguous segments within one receiver session and identical contract; temporary CSV copies on analysis host.'
            write_json(out / name / 'summary.json', result)
            reports.append(dict(path=name, session=group[0]['session'],
                                first_segment=group[0]['segment'], last_segment=group[-1]['segment'],
                                records=counts))
    result = dict(schema='pendulum-recording-collection.v1', complete=True,
                  input_path=str(root), reports=reports, excluded=excluded,
                  time_semantics='Separate reports across sessions, missing segments or changed contracts. No UTC continuity inferred.')
    write_json(out / 'summary.json', result)
    items = ''.join(f'<li><a href="{r["path"]}/report.html">{html.escape(r["session"])}: segments {r["first_segment"]}–{r["last_segment"]}</a></li>' for r in reports)
    omissions = ''.join(f'<li>{html.escape(e["path"])}: {html.escape(e["reason"])}</li>' for e in excluded)
    (out / 'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Recording collection</title><h1>Recording collection</h1>'
        '<p>Contiguous segments are analysed together. Sessions and unknown gaps remain separate.</p><ul>' + items + '</ul>'
        '<h2>Unavailable or excluded segments</h2><ul>' + omissions + '</ul></html>', encoding='utf-8')
    progress(f"Complete: {out / 'report.html'}")
    return result
