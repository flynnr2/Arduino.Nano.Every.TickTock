"""Record → rotate → compress → archive → export → analyse without losing rows."""
import csv
from datetime import datetime, timedelta, timezone
import gzip
import io
import json
import tarfile
import time

from pendulum_pi.config import Settings, save_settings
from pendulum_pi.recording import Recorder
from pendulum_pi.storage import maintain_storage
from pendulum_pi.web import create_app
from pendulum_analysis.suite.collection import run_collection
from pendulum_analysis.suite.common import Settings as AnalysisSettings


def test_recorded_sets_survive_lifecycle_export_and_automatic_analysis_assembly(tmp_path):
    archive = tmp_path / 'archive'
    archive.mkdir()
    cfg = Settings(data_dir=tmp_path / 'data', runtime_dir=tmp_path / 'runtime',
                   archive_dir=archive, min_free_mb=0, file_bytes=4096)
    config_path = tmp_path / 'config.json'
    save_settings(config_path, cfg)
    recorder = Recorder(cfg)
    recorder.start('integration', {'cfg': {'nhz': 16000000}})
    mono, epoch = time.monotonic(), time.time()
    environment = dict(temperature_C=20.0, humidity_pct=50.0, pressure_hPa=1000.0)
    expected = {'CPS': [], 'CSW': []}
    for seq in range(120):
        edge = seq * 16000000
        pps = dict(seq=seq, edge_tcb0=edge, gps_status=2, holdover_age_ms=0,
                   cap16=edge % 65536, latency16=97, now32=edge + 97, drop_pps=0)
        assert recorder.raw(f'CPS,{seq}\n'.encode(), mono + seq, epoch + seq)
        assert recorder.capture('CPS', pps, environment, mono + seq, epoch + seq)
        expected['CPS'].append(seq)
        recorder.tick()
        if seq % 2 == 0:
            swing = dict(seq=seq // 2, drop_ir=0, drop_pps=0, drop_swing=0,
                         **{f'edge{i}_tcb0': edge + i * 8000000 for i in range(5)})
            assert recorder.raw(f'CSW,{seq//2}\n'.encode(), mono + seq, epoch + seq)
            assert recorder.capture('CSW', swing, environment, mono + seq, epoch + seq)
            expected['CSW'].append(seq // 2)
            recorder.tick()
    recorder.close()
    segments = list(cfg.data_dir.glob('*/segment-*'))
    assert len(segments) > 1
    for _ in range(len(segments)):
        status = maintain_storage(cfg)
        assert not status['errors']
    entries = json.loads((cfg.data_dir / 'catalogue.json').read_text())['segments']
    assert len(entries) == len(segments)
    assert all(entry['archive']['verified'] for entry in entries)
    assert all((archive / entry['path'] / 'archive.json').exists() for entry in entries)

    start = datetime.fromtimestamp(epoch, timezone.utc).date()
    client = create_app(config_path).test_client()
    response = client.get('/api/export', query_string={
        'start': start.isoformat(), 'end': (start + timedelta(days=1)).isoformat(), 'kind': 'measurements'})
    assert response.status_code == 200
    actual = {'CPS': [], 'CSW': []}
    with tarfile.open(fileobj=io.BytesIO(response.data), mode='r:gz') as package:
        description = json.load(package.extractfile('export.json'))
        assert description['segment_count'] == len(segments)
        for member in package.getmembers():
            for tag, name in [('CPS', 'PCPS.CSV.gz'), ('CSW', 'PCSW.CSV.gz')]:
                if member.name.endswith('/' + name):
                    contents = gzip.decompress(package.extractfile(member).read()).decode()
                    actual[tag].extend(int(row['seq']) for row in csv.DictReader(io.StringIO(contents)))
    response.close()
    assert actual == expected

    calls = []
    def analyse(source, output, *args):
        output.mkdir(parents=True)
        with (source / 'PCPS.CSV').open() as stream:
            calls.append([int(row['seq']) for row in csv.DictReader(stream)])
        return {'complete': True, 'provenance': {}}
    result = run_collection(cfg.data_dir / 'catalogue.json', tmp_path / 'analysis',
                            AnalysisSettings(), False, lambda _: None, analyse)
    assert len(result['reports']) == 1
    assert calls == [expected['CPS']]
