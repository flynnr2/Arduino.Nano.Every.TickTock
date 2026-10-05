"""LAN dashboard; never opens serial, I²C or the acquisition log writer."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from functools import wraps
import hmac
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from urllib.parse import urlsplit
import uuid

from flask import Flask, Response, abort, jsonify, request

from .common import atomic_json, read_json
from .commands import validate_command
from .config import HOT_FIELDS, load_settings, save_settings, update_settings
from .exports import ExportError, catalogue, plan_export, select_segments, coverage_metadata, timestamp, TIME_NOTE
from .storage import storage_lock
from .history import query_history
from .correlation import query_temperature_correlation
from .views import request_environment
from .results import saved_status

HOT_SETTINGS = HOT_FIELDS
LOG_NAMES = frozenset({"PCSW.CSV", "PCPS.CSV", "STS.CSV", "PI.CSV", "RAW.jsonl", "ANALYSIS.jsonl", "SUMMARY.CSV", "manifest.json", "catalogue.json"})
LOG_NAMES |= frozenset(name + ".gz" for name in LOG_NAMES if name.endswith((".CSV", ".jsonl")))
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")


def _parts(value: str, *, file: bool = False) -> list[str]:
    parts = value.split("/") if value else []
    if len(parts) > 4 or any(not _COMPONENT.fullmatch(p) or p in {".", ".."} for p in parts):
        abort(404)
    if file and (not parts or parts[-1] not in LOG_NAMES):
        abort(404)
    return parts


def _open_directory(root: Path, parts: list[str]) -> int:
    """Walk descriptors, refusing symlinks even if renamed during a request."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _snapshot_file(root: Path, value: str):
    parts = _parts(value, file=True)
    directory = _open_directory(root, parts[:-1])
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    stream = os.fdopen(fd, "rb")
    try:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            abort(404)
        size = info.st_size
        if size and parts[-1].upper().endswith((".CSV", ".JSONL")):
            tail_size = min(size, 1024 * 1024)
            stream.seek(size - tail_size)
            tail = stream.read(tail_size)
            newline = tail.rfind(b"\n")
            if newline < 0:
                abort(409, "No complete log line within the last MiB")
            size = size - tail_size + newline + 1
        stream.seek(0)
        return stream, size, parts[-1]
    except BaseException:
        stream.close()
        raise


@contextmanager
def _read_storage(root):
    """Maintenance can be slow; fail promptly instead of occupying web workers."""
    try:
        with storage_lock(root, exclusive=False, blocking=False):
            yield
    except BlockingIOError:
        raise ExportError('Storage maintenance is running; try the download or catalogue again shortly', 503) from None


def create_app(config_path: str | Path) -> Flask:
    config_path = Path(config_path)
    app = Flask(__name__, static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 8192
    mutation_lock = threading.Lock()
    downloads = threading.BoundedSemaphore(2)
    history_queries = threading.BoundedSemaphore(2)

    def settings():
        return load_settings(config_path)

    def protected(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            token = settings().api_token
            if not token:
                abort(403, "Administration is disabled; configure an API token on the Pi")
            supplied = request.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                abort(401, "An administrator token is required")
            origin = request.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != request.scheme or parsed.netloc != request.host or parsed.path or parsed.query or parsed.fragment:
                    abort(403, "Cross-origin changes are not allowed")
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403, "Cross-site changes are not allowed")
            if not request.is_json:
                abort(415, "Send application/json")
            return fn(*args, **kwargs)
        return wrapped

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(415)
    @app.errorhandler(429)
    @app.errorhandler(503)
    def error_response(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(ExportError)
    def export_error(error):
        return jsonify(error=str(error)), error.status

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/status")
    def status():
        current = settings()
        data = saved_status(current)
        data = dict(data)
        updated = data.get("updated_monotonic")
        age = time.monotonic() - updated if isinstance(updated, (int, float)) else None
        fresh = age is not None and math.isfinite(age) and 0 <= age <= 5
        storage = dict(read_json(current.runtime_dir / "storage.json", {}) or {})
        storage.setdefault("archive_configured", bool(current.archive_dir))
        try:
            storage_age = time.time() - timestamp(storage.get("updated_utc")).timestamp()
        except (ExportError, OverflowError, OSError):
            storage_age = None
        storage["snapshot_age_seconds"] = storage_age
        storage["stale"] = storage_age is None or not 0 <= storage_age <= max(15, 3 * current.storage_interval_seconds)
        data["storage"] = storage
        data["service_stale"] = not fresh
        data["snapshot_age_seconds"] = age if age is not None and math.isfinite(age) else None
        if not fresh and isinstance(data.get('time_health'), dict):
            data['time_health'] = dict(data['time_health'], fresh=False,
                                       status='unavailable', error='Acquisition status is stale')
        if not fresh and isinstance(data.get('gps_health'), dict):
            data['gps_health'] = dict(data['gps_health'], fresh=False,
                                      status='unavailable', fix_mode=None, fix_status=None,
                                      satellites_visible=None, satellites_used=None, hdop=None,
                                      error='Acquisition status is stale')
        logging = data.get("logging", {})
        recording_expected = logging.get("enabled", current.logging_enabled)
        data["healthy"] = bool(fresh and not data.get("stopped")
                               and data.get("connected") and data.get("ready")
                               and not data.get("capture_stale", True)
                               and not data.get("config_error") and not logging.get("error")
                               and (not recording_expected or logging.get("active")))
        return jsonify(data)

    @app.get('/api/phase')
    def phase():
        if request.args:
            abort(400, 'The phase view uses the latest recording segment')
        data = read_json(settings().runtime_dir / 'phase.json',
                         {'charts': [], 'state': 'waiting', 'message': 'Waiting for saved phase analysis.'})
        if data.get('published_monotonic') is not None and time.monotonic() - data['published_monotonic'] > 10:
            data = dict(data, stale=True, updating=False, live=False,
                        message='Saved-view service is unavailable; showing its last result.')
        return jsonify(data)

    @app.get('/api/history')
    def history():
        if set(request.args) - {'start', 'end', 'session', 'series', 'max_points'}:
            abort(400, 'Unsupported history query parameter')
        try:
            end = float(request.args.get('end', time.time()))
            start = float(request.args.get('start', end - 3600))
            budget = int(request.args.get('max_points', 1000))
        except (TypeError, ValueError, OverflowError):
            abort(400, 'Invalid history range or point budget')
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end
                and end - start <= 366 * 86400 and 32 <= budget <= 2000):
            abort(400, 'Use a finite range up to 366 days and a point budget from 32 to 2000')
        session = request.args.get('session') or None
        if session and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', session):
            abort(400, 'Invalid history session')
        series = request.args.get('series')
        series = series.split(',') if series else None
        if series is not None and (len(series) > 9 or any(len(name) > 40 for name in series)):
            abort(400, 'Invalid history series')
        if not history_queries.acquire(blocking=False):
            abort(429, 'Two history queries are running; try again shortly')
        try:
            return jsonify(query_history(settings().data_dir, start, end,
                                         session=session, series=series, max_points=budget))
        except ValueError as error:
            abort(400, str(error))
        except (OSError, sqlite3.Error):
            abort(503, 'History is temporarily unavailable; acquisition can continue')
        finally:
            history_queries.release()

    @app.get('/api/history/environment')
    def environmental_relationships():
        if set(request.args) - {'start', 'end', 'session'}:
            abort(400, 'Unsupported environmental query parameter')
        try:
            end = float(request.args.get('end', time.time()))
            start = float(request.args.get('start', end - 3600))
        except (TypeError, ValueError, OverflowError):
            abort(400, 'Invalid environmental comparison range')
        if not history_queries.acquire(blocking=False):
            abort(429, 'Two history queries are running; try again shortly')
        try:
            return jsonify(request_environment(
                settings(), start, end, session=request.args.get('session') or None))
        except ValueError as error:
            abort(400, str(error))
        except (OSError, sqlite3.Error):
            abort(503, 'Environmental comparison is temporarily unavailable; try a shorter range')
        finally:
            history_queries.release()

    @app.get('/api/history/temperature')
    def temperature_correlation():
        if set(request.args) - {'start', 'end', 'session', 'estimate'}:
            abort(400, 'Unsupported temperature query parameter')
        try:
            end = float(request.args.get('end', time.time()))
            start = float(request.args.get('start', end - 3600))
        except (TypeError, ValueError, OverflowError):
            abort(400, 'Invalid temperature comparison range')
        if not history_queries.acquire(blocking=False):
            abort(429, 'Two history queries are running; try again shortly')
        try:
            return jsonify(query_temperature_correlation(
                settings().data_dir, start, end, session=request.args.get('session') or None,
                estimate=request.args.get('estimate', 'window')))
        except ValueError as error:
            abort(400, str(error))
        except (OSError, sqlite3.Error):
            abort(503, 'Temperature comparison is temporarily unavailable; try a shorter range')
        finally:
            history_queries.release()

    @app.get("/api/config")
    def get_config():
        current = settings()
        data = asdict(current)
        data.pop("api_token", None)
        data = {k: str(v) if isinstance(v, Path) else v for k, v in data.items()}
        return jsonify(settings=data, editable=sorted(HOT_SETTINGS), administration_enabled=bool(current.api_token))

    @app.post("/api/config")
    @protected
    def set_config():
        changes = request.get_json()
        if not isinstance(changes, dict) or not changes or set(changes) - HOT_SETTINGS:
            abort(400, "Only the documented live settings can be changed")
        with mutation_lock:
            try:
                updated = update_settings(settings(), changes, hot_only=True)
                save_settings(config_path, updated)
            except (TypeError, ValueError) as error:
                abort(400, str(error))
        return jsonify(saved=True, applies="within one second")

    @app.post("/api/commands")
    @protected
    def command():
        body = request.get_json()
        text = body.get("command") if isinstance(body, dict) and set(body) == {"command"} else None
        try:
            text = validate_command(text)
        except ValueError as error:
            abort(400, str(error))
        with mutation_lock:
            runtime = settings().runtime_dir
            queue = runtime / "commands"
            results = runtime / "results"
            queue.mkdir(parents=True, exist_ok=True)
            results.mkdir(parents=True, exist_ok=True)
            # Queued requests plus recently sent requests count towards the bound.
            pending = set()
            with os.scandir(queue) as entries:
                for count, entry in enumerate(entries):
                    if count >= 512:
                        abort(503, "Command storage needs acquisition-service maintenance")
                    if entry.name.endswith(".json"):
                        pending.add(entry.name)
                    if len(pending) >= 32:
                        abort(429, "Nano command queue is full")
            with os.scandir(results) as entries:
                for count, entry in enumerate(entries):
                    if count >= 512:
                        abort(503, "Command storage needs acquisition-service maintenance")
                    if entry.name.endswith(".json"):
                        result = read_json(Path(entry.path), {}) or {}
                        if result.get("state") in {"queued", "sent"} and 0 <= time.monotonic() - result.get("updated_monotonic", 0) <= 30:
                            pending.add(entry.name)
                    if len(pending) >= 32:
                        abort(429, "Nano command queue is full")
            ident = uuid.uuid4().hex
            now = time.monotonic()
            atomic_json(results / f"{ident}.json", {"id": ident, "state": "queued", "command": text, "updated_monotonic": now})
            atomic_json(queue / f"{ident}.json", {"id": ident, "command": text, "created_monotonic": now})
        return jsonify(id=ident, state="queued"), 202

    @app.get("/api/commands/<ident>")
    def command_result(ident):
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            abort(404)
        result = read_json(settings().runtime_dir / "results" / f"{ident}.json", None)
        if result is None:
            abort(404)
        if result.get("state") in {"queued", "sent"} and time.monotonic() - result.get("updated_monotonic", 0) > 30:
            result = dict(result, state="timeout", response="No acquisition acknowledgement within 30 seconds; inspect Nano state before retrying")
        return jsonify(result)

    @app.get("/api/catalogue")
    def get_catalogue():
        current = settings()
        with _read_storage(current.data_dir):
            data = catalogue(current.data_dir)
        segments = data["segments"]
        start, end = request.args.get("start"), request.args.get("end")
        if start or end:
            segments = select_segments(data, start, end)
        try:
            offset, limit = int(request.args.get("cursor", "0")), int(request.args.get("limit", "50"))
        except ValueError:
            abort(400, "Invalid pagination")
        if not 0 <= offset <= 1000000 or not 1 <= limit <= 100:
            abort(400, "Invalid pagination")
        segments = sorted(segments, key=lambda s: (s.get("started_utc", ""), s.get("path", "")), reverse=True)
        page = [{k: segment.get(k) for k in ("id", "path", "session", "segment", "started_utc", "last_record_utc", "closed_utc", "files", "archive")}
                for segment in segments[offset:offset + limit]]
        for result, source in zip(page, segments[offset:offset + limit]):
            result.update(coverage_metadata(source))
        return jsonify(segments=page, total=len(segments), updated_utc=data.get("updated_utc"),
                       time_semantics=TIME_NOTE,
                       next_cursor=offset + limit if offset + limit < len(segments) else None)

    def export_plan(current):
        return plan_export(current.data_dir, catalogue(current.data_dir),
                           request.args.get("start"), request.args.get("end"),
                           request.args.get("kind", "measurements"), current.export_max_mb * 1024**2)

    @app.get("/api/export/estimate")
    def estimate_export():
        current = settings()
        with _read_storage(current.data_dir):
            plan = export_plan(current)
        return jsonify(plan.description)

    @app.get("/api/export")
    def export():
        if not downloads.acquire(blocking=False):
            abort(429, "Two downloads are already running; try again shortly")
        current = settings()
        lock = _read_storage(current.data_dir)
        entered = False
        try:
            lock.__enter__()
            entered = True
            plan = export_plan(current)
        except BaseException:
            if entered:
                lock.__exit__(None, None, None)
            downloads.release()
            raise
        closed = False
        def close():
            nonlocal closed
            if not closed:
                closed = True
                lock.__exit__(None, None, None)
                downloads.release()
        def chunks():
            try:
                yield from plan.chunks()
            finally:
                close()
        response = Response(chunks(), mimetype="application/gzip")
        response.headers["Content-Disposition"] = 'attachment; filename="pendulum-' + plan.description["kind"] + '.tar.gz"'
        response.headers["X-Export-Maximum-Bytes"] = str(plan.description["maximum_download_bytes"])
        response.call_on_close(close)
        return response

    @app.get("/api/files")
    def files():
        path = request.args.get("path", "")
        parts = _parts(path)
        try:
            offset = int(request.args.get("cursor", "0"))
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            abort(400, "Invalid pagination")
        if not 0 <= offset <= 1000000 or not 1 <= limit <= 100:
            abort(400, "Invalid pagination")
        entries_out = []
        next_cursor = None
        try:
            fd = _open_directory(settings().data_dir, parts)
            try:
                with os.scandir(fd) as entries:
                    for index, entry in enumerate(entries):
                        if index < offset:
                            continue
                        if len(entries_out) >= limit or index - offset >= 1000:
                            next_cursor = index
                            break
                        if entry.is_symlink() or not _COMPONENT.fullmatch(entry.name):
                            continue
                        directory = entry.is_dir(follow_symlinks=False)
                        if (directory and len(parts) < 3) or (entry.is_file(follow_symlinks=False) and entry.name in LOG_NAMES):
                            info = entry.stat(follow_symlinks=False)
                            entries_out.append({"name": entry.name, "path": "/".join(parts + [entry.name]), "directory": directory, "bytes": None if directory else info.st_size, "modified": info.st_mtime})
            finally:
                os.close(fd)
        except FileNotFoundError:
            if not parts:
                return jsonify(path=path, entries=[], next_cursor=None)
            abort(404)
        except OSError:
            abort(404)
        return jsonify(path=path, entries=entries_out, next_cursor=next_cursor)

    @app.get("/api/download/<path:value>")
    def download(value):
        if not downloads.acquire(blocking=False):
            abort(429, "Two downloads are already running; try again shortly")
        lock = _read_storage(settings().data_dir)
        entered = False
        try:
            lock.__enter__()
            entered = True
            stream, length, filename = _snapshot_file(settings().data_dir, value)
        except BaseException as error:
            if entered:
                lock.__exit__(None, None, None)
            downloads.release()
            if isinstance(error, OSError):
                abort(404)
            raise
        closed = False
        def close():
            nonlocal closed
            if not closed:
                closed = True
                stream.close()
                lock.__exit__(None, None, None)
                downloads.release()
        def chunks():
            remaining = length
            try:
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                close()
        response = Response(chunks(), mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.content_length = length
        response.call_on_close(close)
        return response

    return app


def run_web(settings, config_path: str | Path):
    from waitress import serve
    serve(create_app(config_path), host=settings.web_host, port=settings.web_port,
          threads=6, connection_limit=24, backlog=24, channel_timeout=30,
          max_request_body_size=8192, max_request_header_size=16384,
          outbuf_overflow=65536, outbuf_high_watermark=131072,
          channel_request_lookahead=1, expose_tracebacks=False)
