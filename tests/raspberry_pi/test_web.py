"""HTTP integration tests, including bounded live-file snapshots."""
from dataclasses import replace
from pathlib import Path
import sqlite3
import time

import pytest

from pendulum_pi.common import atomic_json
from pendulum_pi.config import Settings, load_settings, save_settings
from pendulum_pi.web import create_app

TOKEN = "test-token-for-local-administration"


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / "config.json"
    settings = Settings(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", api_token=TOKEN)
    settings.data_dir.mkdir()
    settings.runtime_dir.mkdir()
    save_settings(config, settings)
    app = create_app(config)
    app.testing = True
    return app.test_client(), config, settings


def headers(**kwargs):
    return {"Authorization": "Bearer " + TOKEN, **kwargs}


def test_status_flags_missing_stale_and_fresh(setup):
    client, _, settings = setup
    assert client.get("/api/status").json["service_stale"]
    state = {"updated_monotonic": time.monotonic(), "connected": True, "ready": True, "capture_stale": False, "logging": {"active": True, "error": None}}
    atomic_json(settings.runtime_dir / "status.json", state)
    assert client.get("/api/status").json["healthy"]
    state["updated_monotonic"] -= 6
    atomic_json(settings.runtime_dir / "status.json", state)
    assert not client.get("/api/status").json["healthy"]
    state["updated_monotonic"] = time.monotonic() + 100
    atomic_json(settings.runtime_dir / "status.json", state)
    assert client.get("/api/status").json["service_stale"]


def test_configuration_protected_validated_and_redacted(setup):
    client, config, settings = setup
    response = client.get("/api/config")
    assert "api_token" not in response.json["settings"]
    assert TOKEN not in response.text
    assert client.post("/api/config", json={"logging_enabled": False}).status_code == 401
    assert client.post("/api/config", json={"logging_enabled": False}, headers=headers(Origin="https://elsewhere.example")).status_code == 403
    assert client.post("/api/config", json={"logging_enabled": False}, headers=headers(**{"Sec-Fetch-Site": "cross-site"})).status_code == 403
    for invalid in ({"logging_enabled": "false"}, {"segment_bytes": 1}, {"serial_port": "/dev/elsewhere"}, {"flush_seconds": True}, {"short_minutes": 31}, [], {}):
        assert client.post("/api/config", json=invalid, headers=headers()).status_code == 400
    assert client.post("/api/config", json={"logging_enabled": False}, headers=headers(Origin="http://localhost")).status_code == 200
    assert not load_settings(config).logging_enabled
    save_settings(config, replace(settings, api_token=""))
    assert client.post("/api/config", json={"logging_enabled": True}, headers=headers()).status_code == 403


def test_observatory_settings_and_bounded_history_api(setup):
    client, config, _ = setup
    assert client.get('/api/config').json['settings']['target_period_s'] is None
    assert client.post('/api/config', json={'target_period_s': 2}, headers=headers()).status_code == 200
    assert load_settings(config).target_period_s == 2
    for patch in ({'target_period_s': 0}, {'target_period_s': True}, {'history_max_mb': 0},
                  {'history_retention_days': 0}):
        assert client.post('/api/config', json=patch, headers=headers()).status_code == 400
    assert client.post('/api/config', json={'target_period_s': None}, headers=headers()).status_code == 200
    result = client.get('/api/history')
    assert result.status_code == 200
    assert result.json['points'] == []
    for query in ('start=nan', 'end=inf', 'start=-1', 'start=5&end=4',
                  'start=0&end=999999999', 'max_points=99999999', 'max_points=1',
                  'session=../../private', 'unknown=true', 'series=unknown'):
        assert client.get('/api/history?' + query).status_code == 400


def test_time_health_cannot_remain_fresh_when_acquisition_stops(setup):
    client, _, settings = setup
    atomic_json(settings.runtime_dir / 'status.json', {
        'updated_monotonic': time.monotonic() - 10,
        'time_health': {'status': 'synchronized', 'source': 'NTP', 'fresh': True},
        'gps_health': {'status': 'available', 'fresh': True, 'fix_mode': 3,
                       'satellites_used': 7, 'satellites_visible': 9}})
    response = client.get('/api/status').json
    health = response['time_health']
    assert not health['fresh']
    assert health['status'] == 'unavailable'
    assert response['gps_health']['status'] == 'unavailable'
    assert response['gps_health']['fix_mode'] is None
    assert response['gps_health']['satellites_used'] is None


def test_temperature_correlation_api_validation_and_unavailable_history(setup, monkeypatch):
    client, _, _ = setup
    response = client.get('/api/history/temperature')
    assert response.status_code == 200
    assert response.json['segments'] == []
    assert response.json['estimate'] == 'window'
    for query in ('start=nan', 'end=inf', 'start=-1', 'start=5&end=4',
                  'start=0&end=999999999', 'session=../../private',
                  'estimate=unknown', 'max_points=20', 'start=bad'):
        assert client.get('/api/history/temperature?' + query).status_code == 400
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('interrupted')
    monkeypatch.setattr('pendulum_pi.web.query_temperature_correlation', unavailable)
    # Errors release the query slot; subsequent requests must not get 429.
    for _ in range(3):
        assert client.get('/api/history/temperature').status_code == 503


def test_command_validation_enqueue_and_timeout(setup):
    client, _, settings = setup
    for command in ("reset defaults", "get X\nreset defaults", "get X\r", "set X nan", "set X -1", "set X +1", "set X 1.5", "set X 4294967296", "set X 1;reset", "emit anything"):
        assert client.post("/api/commands", json={"command": command}, headers=headers()).status_code == 400
    response = client.post("/api/commands", json={"command": "get ppsFastShift"}, headers=headers())
    assert response.status_code == 202
    ident = response.json["id"]
    assert (settings.runtime_dir / "commands" / f"{ident}.json").exists()
    assert client.get(f"/api/commands/{ident}").json["state"] == "queued"
    atomic_json(settings.runtime_dir / "results" / f"{ident}.json", {"id": ident, "state": "sent", "updated_monotonic": time.monotonic() - 31})
    assert client.get(f"/api/commands/{ident}").json["state"] == "timeout"
    assert client.get("/api/commands/bogus").status_code == 404
    for index in range(32):
        atomic_json(settings.runtime_dir / "commands" / f"{index:032x}.json", {})
    assert client.post("/api/commands", json={"command": "emit meta"}, headers=headers()).status_code == 429


def test_file_listing_paginated_and_confined(setup, tmp_path):
    client, _, settings = setup
    for index in range(7):
        folder = settings.data_dir / f"session-{index}"
        folder.mkdir()
        (folder / "PCSW.CSV").write_text("header\nrecord\n")
    (settings.data_dir / "secret.txt").write_text("secret")
    (settings.data_dir / "session-link").symlink_to(tmp_path, target_is_directory=True)
    first = client.get("/api/files?limit=3").json
    assert len(first["entries"]) == 3
    second = client.get(f'/api/files?limit=3&cursor={first["next_cursor"]}').json
    assert len(second["entries"]) == 3
    assert not {item["path"] for item in first["entries"]} & {item["path"] for item in second["entries"]}
    assert len(client.get("/api/files").json["entries"]) == 7
    for path in ("../", "session-link", "/tmp", "session-0/../../"):
        assert client.get("/api/files", query_string={"path": path}).status_code == 404
    assert client.get("/api/download/secret.txt").status_code == 404
    (settings.data_dir / "PI.CSV").symlink_to(tmp_path / "config.json")
    assert client.get("/api/download/PI.CSV").status_code == 404
    assert client.get("/api/download/session-link/config.json").status_code == 404


def test_download_snapshot_excludes_partial_tail_and_concurrent_append(setup):
    client, _, settings = setup
    folder = settings.data_dir / "session-1" / "segment-000001"
    folder.mkdir(parents=True)
    path = folder / "PCSW.CSV"
    original = b"header\n" + b"record\n" * 20000
    path.write_bytes(original + b"unfinished")
    response = client.get("/api/download/session-1/segment-000001/PCSW.CSV", buffered=False)
    with path.open("ab") as writer:
        writer.write(b" completed\nnew record\n")
    received = b"".join(response.response)
    assert received == original
    assert int(response.headers["Content-Length"]) == len(original)
    response.close()
    assert client.get("/api/download/session-1/segment-000001/PCSW.CSV").data.endswith(b"new record\n")


def test_download_concurrency_does_not_block_status_and_releases_on_close(setup):
    client, _, settings = setup
    (settings.data_dir / "PCSW.CSV").write_bytes(b"record\n" * 20000)
    first = client.get("/api/download/PCSW.CSV", buffered=False)
    second = client.get("/api/download/PCSW.CSV", buffered=False)
    assert client.get("/api/download/PCSW.CSV").status_code == 429
    assert client.get("/api/status").status_code == 200
    first.close()
    third = client.get("/api/download/PCSW.CSV", buffered=False)
    assert third.status_code == 200
    second.close()
    third.close()


def test_static_dashboard_has_local_assets_and_security_headers(setup):
    client, _, _ = setup
    response = client.get("/")
    assert response.status_code == 200
    assert 'src="/static/app.js"' in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/correlation.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


@pytest.mark.parametrize("change,healthy", [
    ({"capture_stale": True}, False),
    ({"capture_stale": False}, True),
    ({"config_error": "invalid configuration"}, False),
    ({"stopped": True}, False),
    ({"connected": False}, False),
    ({"ready": False}, False),
    ({"logging": {"enabled": True, "active": False, "error": None}}, False),
    ({"logging": {"enabled": False, "active": False, "error": None}}, True),
    ({"logging": {"enabled": True, "active": True, "error": "storage full"}}, False),
])
def test_health_reflects_capture_configuration_and_expected_recording(setup, change, healthy):
    client, _, settings = setup
    state = {"updated_monotonic": time.monotonic(), "source": "serial", "connected": True,
             "ready": True, "capture_stale": False, "config_error": None,
             "logging": {"enabled": True, "active": True, "error": None}}
    state.update(change)
    atomic_json(settings.runtime_dir / "status.json", state)
    assert client.get("/api/status").json["healthy"] is healthy


@pytest.mark.parametrize("source", ["serial", "demo", "replay"])
def test_status_retains_acquisition_source_for_dashboard_banner(setup, source):
    client, _, settings = setup
    atomic_json(settings.runtime_dir / "status.json", {"source": source, "updated_monotonic": time.monotonic()})
    assert client.get("/api/status").json["source"] == source


@pytest.mark.parametrize("command", ["set ppsFastShift 0", "set ppsFastShift 4294967295", "get ppsFastShift", "emit startup", "repair eeprom"])
def test_unsigned_command_forms_use_shared_validator(setup, command):
    client, _, _ = setup
    assert client.post("/api/commands", json={"command": command}, headers=headers()).status_code == 202


def test_phase_api_is_read_only_and_reports_missing_recordings(setup):
    client, _, _ = setup
    result = client.get('/api/phase')
    assert result.status_code == 200
    assert result.json['state'] == 'waiting'
    assert result.json['charts'] == []
    assert client.get('/api/phase?path=../../private').status_code == 400
    assert client.post('/api/phase').status_code == 405
    assert client.get('/static/phase.js').status_code == 200


def test_environmental_api_validation_and_unavailable_history(setup, monkeypatch):
    client, _, _ = setup
    response = client.get('/api/history/environment')
    assert response.status_code == 200 and response.json['segments'] == []
    assert response.json['state'] == 'updating'
    assert response.json['cache_end'] % 60 == 0
    for query in ('start=nan','end=inf','start=oops','start=5&end=4',
                  'session=../bad','unknown=true','estimate=short','start=0&end=999999999'):
        assert client.get('/api/history/environment?'+query).status_code == 400
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('interrupted')
    monkeypatch.setattr('pendulum_pi.web.request_environment', unavailable)
    assert client.get('/api/history/environment').status_code == 503
    assert client.get('/api/history/environment').status_code == 503  # Semaphore released.


def test_phase_keeps_saved_charts_when_view_service_stops(setup):
    from pendulum_pi.common import atomic_json
    client, _, settings = setup
    atomic_json(settings.runtime_dir/'phase.json',
                {'charts': [{'metric': 'full', 'points': []}], 'state': 'ready',
                 'published_monotonic': time.monotonic()-20})
    data = client.get('/api/phase').json
    assert data['stale'] and not data['live']
    assert data['charts'] == [{'metric': 'full', 'points': []}]
