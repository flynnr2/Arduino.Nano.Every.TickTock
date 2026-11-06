"""Laptop capture acceptance tests, using serial chunks without hardware."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nano_capture_test", ROOT / "tools/nano_capture.py")
assert SPEC is not None and SPEC.loader is not None
nano = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nano
SPEC.loader.exec_module(nano)

CFG = b"CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=test\n"
SW_SCHEMA = b"SCH,CSW,canonical_swing_v2,seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing\n"
PS_SCHEMA = b"SCH,CPS,canonical_pps_v1,seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps\n"
META = CFG + SW_SCHEMA + PS_SCHEMA
SWING = b"CSW,1,100,120,150,180,205,0,0,0\n"
PPS = b"CPS,1,16000000,2,0,12345,9,16000004,0\n"
OUTPUTS = {"PCSW.CSV", "PCPS.CSV", "STS.CSV", "CFG.CSV", "HDR.CSV", "wire_raw.log"}


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_capture_files_load_in_analysis_suite(tmp_path):
    from pendulum_analysis.suite.common import discover, read_numeric
    from pendulum_analysis.suite.clock import REQUIRED as PPS_REQUIRED
    from pendulum_analysis.suite.swings import REQUIRED as SWING_REQUIRED

    wire = (ROOT / "tests/fixtures/nano_capture/valid_canonical_serial.txt").read_bytes()
    with nano.Capture(tmp_path) as capture:
        capture.feed(wire)
        assert capture.ready
        assert capture.counts["CSW"] == capture.counts["CPS"] == 1
        assert capture.malformed == capture.before_ready == 0
    assert {p.name for p in tmp_path.iterdir()} == OUTPUTS
    assert (tmp_path / "wire_raw.log").read_bytes() == wire
    found = discover(tmp_path)
    for key, required in (("pcps", PPS_REQUIRED), ("pcsw", SWING_REQUIRED)):
        frame = read_numeric(found[key], required)
        assert len(frame) == 1
        assert frame.attrs["invalid_fields"] == []
        for environmental in ("temperature_C", "humidity_pct", "pressure_hPa"):
            assert environmental not in frame or frame[environmental].isna().all()


def test_partial_reads_crlf_and_invalid_bytes_are_preserved(tmp_path):
    wire = (META + SWING + b"\xffgarbage\n" + PPS).replace(b"\n", b"\r\n")
    # A serial timeout is an empty chunk, not the end of a record.
    with nano.Capture(tmp_path) as capture:
        for byte in wire:
            capture.feed(bytes([byte]))
            capture.feed(b"")
        assert capture.counts["CSW"] == capture.counts["CPS"] == 1
        assert capture.malformed == 1
    assert (tmp_path / "wire_raw.log").read_bytes() == wire
    assert rows(tmp_path / "PCSW.CSV")[0]["edge4_tcb0"] == "205"


def test_data_waits_for_cfg_and_both_schemas_and_allows_replay(tmp_path):
    wire = SWING + CFG + SW_SCHEMA + PPS + PS_SCHEMA + SWING + PPS + META + SWING
    with nano.Capture(tmp_path) as capture:
        capture.feed(SWING + CFG + SW_SCHEMA + PPS)
        assert not capture.ready
        assert capture.before_ready == 2
        capture.feed(PS_SCHEMA + SWING + PPS + META + SWING)
        assert capture.ready
        assert capture.counts["CSW"] == 2
        assert capture.counts["CPS"] == 1
        assert capture.malformed == 0
    assert (tmp_path / "wire_raw.log").read_bytes() == wire
    assert len(rows(tmp_path / "PCSW.CSV")) == 2


@pytest.mark.parametrize("row", [
    b"CSW,1,100\n",
    SWING.replace(b",100,", b",-1,"),
    SWING.replace(b",100,", b",4294967296,"),
    SWING.replace(b",100,", b",1.5,"),
    SWING.replace(b",100,", b",1e3,"),
    SWING.replace(b",100,", b",nan,"),
    PPS.replace(b",12345,", b",65536,"),
    PPS.replace(b",9,", b",65536,"),
    PPS.replace(b",2,0,", b",4,0,"),
    PPS.replace(b",2,0,", b",-1,0,"),
    PPS.replace(b",16000004,", b",4294967296,"),
    b'CSW,"unfinished\n',
    b"UNRECOGNISED,1\n",
])
def test_malformed_rows_are_skipped_and_capture_continues(tmp_path, row):
    wire = META + row + SWING + PPS
    with nano.Capture(tmp_path) as capture:
        capture.feed(wire)
        assert capture.malformed == 1
        assert capture.counts["CSW"] == capture.counts["CPS"] == 1
    assert len(rows(tmp_path / "PCSW.CSV")) == len(rows(tmp_path / "PCPS.CSV")) == 1
    assert (tmp_path / "wire_raw.log").read_bytes() == wire


def test_integer_boundaries_remain_exact_and_are_not_unwrapped(tmp_path):
    swing = b"CSW,4294967295,4294967295,0,1,2,3,4294967295,0,4294967295\n"
    pps = b"CPS,4294967295,4294967295,3,4294967295,65535,65535,0,4294967295\n"
    with nano.Capture(tmp_path) as capture:
        capture.feed(META + swing + pps)
        assert capture.malformed == 0
    assert rows(tmp_path / "PCSW.CSV")[0]["edge0_tcb0"] == "4294967295"
    assert rows(tmp_path / "PCSW.CSV")[0]["edge1_tcb0"] == "0"
    assert rows(tmp_path / "PCPS.CSV")[0]["latency16"] == "65535"


@pytest.mark.parametrize("cfg", [
    CFG.replace(b"pv=3", b"pv=3,pv=3"),
    CFG.replace(b"pv=3", b"pv=3,bare-token"),
    CFG.replace(b"pv=3", b"pv=3,=value"),
])
def test_invalid_cfg_token_syntax_does_not_establish_readiness(tmp_path, cfg):
    with nano.Capture(tmp_path) as capture:
        capture.feed(cfg + SW_SCHEMA + PS_SCHEMA + SWING)
        assert not capture.ready
        assert capture.malformed == 1
        assert capture.before_ready == 1
        capture.feed(CFG + SWING)
        assert capture.ready
        assert capture.counts["CSW"] == 1


@pytest.mark.parametrize("change", [
    CFG.replace(b"pv=3", b"pv=4"),
    CFG.replace(b"nhz=16000000", b"nhz=8000000"),
    CFG.replace(b"fw=test", b"fw=different"),
    SW_SCHEMA.replace(b"canonical_swing_v2", b"canonical_swing_v3"),
    PS_SCHEMA.replace(b"seq,edge_tcb0", b"edge_tcb0,seq"),
])
def test_contract_change_is_fatal_but_received_bytes_are_preserved(tmp_path, change):
    wire = META + SWING + change + PPS
    with nano.Capture(tmp_path) as capture:
        with pytest.raises(nano.CaptureError):
            capture.feed(wire)
    assert (tmp_path / "wire_raw.log").read_bytes() == wire
    assert len(rows(tmp_path / "PCSW.CSV")) == 1
    assert rows(tmp_path / "PCPS.CSV") == []


def test_unterminated_record_stays_raw_without_becoming_a_csv_row(tmp_path):
    wire = META + SWING.rstrip(b"\n")
    with nano.Capture(tmp_path) as capture:
        capture.feed(wire)
        capture.finish()
        capture.finish()  # Explicit completion and context-manager cleanup are safe together.
        assert capture.malformed == 1
    assert rows(tmp_path / "PCSW.CSV") == []
    assert (tmp_path / "wire_raw.log").read_bytes() == wire


@pytest.mark.parametrize("filename", sorted(OUTPUTS))
def test_existing_output_prevents_any_overwrite_or_partial_capture(tmp_path, filename):
    (tmp_path / filename).write_bytes(b"previous recording\x00\xff")
    with pytest.raises((FileExistsError, nano.CaptureError)):
        with nano.Capture(tmp_path):
            pytest.fail("Existing output must prevent capture")
    assert {p.name for p in tmp_path.iterdir()} == {filename}
    assert (tmp_path / filename).read_bytes() == b"previous recording\x00\xff"


def test_existing_unrelated_files_are_allowed(tmp_path):
    (tmp_path / "notes.txt").write_text("my pendulum")
    with nano.Capture(tmp_path) as capture:
        capture.feed(META + SWING)
    assert (tmp_path / "notes.txt").read_text() == "my pendulum"


def port(device, description, product=None):
    return SimpleNamespace(device=device, description=description, product=product,
                           manufacturer="Arduino", hwid="USB VID:PID=2341:0058", vid=0x2341, pid=0x0058)


def test_port_selection_uses_identification_not_enumeration_order():
    other = port("/dev/other", "USB Serial Device")
    target = port("/dev/nano", "ARDUINO NANO EVERY")
    assert nano.select_port([other, target]) == "/dev/nano"
    assert nano.select_port([other], requested="COM19") == "COM19"
    assert nano.select_port([], requested="COM19") == "COM19"
    assert nano.select_port([port("COM7", "USB Serial", "Arduino Nano Every")]) == "COM7"


@pytest.mark.parametrize("ports", [[], [port("COM1", "USB Serial")],
                                     [port("COM1", "Nano Every"), port("COM2", "Nano Every")]])
def test_autodetection_requires_exactly_one_confident_match(ports):
    with pytest.raises(nano.CaptureError):
        nano.select_port(ports)


def test_port_listing_includes_device_and_description():
    listing = nano.port_listing([port("COM7", "Arduino Nano Every"), port("COM8", "USB Serial")])
    for expected in ("COM7", "Arduino Nano Every", "COM8", "USB Serial"):
        assert expected in listing


class FakeSerial:
    """Each read advances a deterministic monotonic clock by one second."""

    def __init__(self, chunks=(), final=KeyboardInterrupt):
        self.now = 0.0
        self.chunks = iter(chunks)
        self.final = final
        self.writes = []

    def clock(self):
        return self.now

    def read(self, size):
        assert size > 0
        self.now += 1
        try:
            return next(self.chunks)
        except StopIteration:
            raise self.final()

    def write(self, data):
        self.writes.append((self.now, data))
        return len(data)

    def flush(self):
        pass


@pytest.mark.parametrize("passive", [False, True])
def test_startup_request_and_passive_mode(tmp_path, passive):
    serial = FakeSerial([META + SWING, b"", PPS])
    with nano.Capture(tmp_path) as capture:
        with pytest.raises(KeyboardInterrupt):
            nano.run_capture(serial, capture, passive=passive, clock=serial.clock, report=lambda _: None)
        assert capture.counts["CSW"] == capture.counts["CPS"] == 1
    assert serial.writes == ([] if passive else [(0.0, b"emit meta\n")])


def test_readiness_deadline_retains_raw_and_bounds_metadata_requests(tmp_path):
    serial = FakeSerial([SWING] * 20)
    with nano.Capture(tmp_path) as capture:
        with pytest.raises(nano.CaptureError):
            nano.run_capture(serial, capture, clock=serial.clock, report=lambda _: None)
        assert not capture.ready
        assert capture.before_ready > 0
    assert serial.now <= 11
    assert (tmp_path / "wire_raw.log").read_bytes() == SWING * capture.before_ready
    assert serial.writes == [(0.0, b"emit meta\n"), (3.0, b"emit meta\n"), (6.0, b"emit meta\n")]


def test_passive_waits_for_late_metadata_without_sending_commands(tmp_path):
    serial = FakeSerial([SWING] * 15 + [META + PPS])
    with nano.Capture(tmp_path) as capture:
        with pytest.raises(KeyboardInterrupt):
            nano.run_capture(serial, capture, passive=True, clock=serial.clock, report=lambda _: None)
        assert capture.ready
        assert capture.before_ready == 15
        assert capture.counts["CPS"] == 1
    assert serial.writes == []


def test_metadata_retry_survives_boot_delay_and_stops_when_ready(tmp_path):
    serial = FakeSerial([SWING, b"", b"", META[:23], b"", META[23:], SWING, PPS])
    with nano.Capture(tmp_path) as capture:
        with pytest.raises(KeyboardInterrupt):
            nano.run_capture(serial, capture, clock=serial.clock, report=lambda _: None)
        assert capture.ready
        assert capture.before_ready == 1
        assert capture.counts["CSW"] == capture.counts["CPS"] == 1
    assert serial.writes == [(0.0, b"emit meta\n"), (3.0, b"emit meta\n")]


def test_serial_disconnect_propagates_and_capture_closes_preserving_data(tmp_path):
    serial = FakeSerial([META + SWING + PPS], final=OSError)
    with pytest.raises(OSError):
        with nano.Capture(tmp_path) as capture:
            nano.run_capture(serial, capture, clock=serial.clock, report=lambda _: None)
    assert len(rows(tmp_path / "PCSW.CSV")) == len(rows(tmp_path / "PCPS.CSV")) == 1
    assert (tmp_path / "wire_raw.log").read_bytes() == META + SWING + PPS


def test_malformed_metadata_invalidates_readiness_until_contract_replays(tmp_path):
    malformed = b"CFG,pv=3,unfinished\n"
    with nano.Capture(tmp_path) as capture:
        capture.feed(META + SWING + malformed + PPS)
        assert not capture.ready
        assert capture.before_ready == 1
        capture.feed(META + PPS)
        assert capture.ready
        assert capture.counts["CSW"] == capture.counts["CPS"] == 1
        assert capture.malformed == 1


def test_long_unterminated_input_is_bounded_then_capture_recovers(tmp_path):
    oversized = b"noise," + b"x" * 10000
    with nano.Capture(tmp_path) as capture:
        capture.feed(oversized)
        capture.feed(b"remaining fragment\n" + META + SWING)
        assert capture.ready
        assert capture.malformed == 1
        assert capture.counts["CSW"] == 1
    assert (tmp_path / "wire_raw.log").read_bytes() == oversized + b"remaining fragment\n" + META + SWING


def test_overlong_record_is_counted_once_if_capture_stops_before_newline(tmp_path):
    wire = b"noise," + b"x" * 10000
    with nano.Capture(tmp_path) as capture:
        capture.feed(wire)
        capture.feed(b"unfinished tail")
        capture.finish()
        assert capture.malformed == capture.lines == 1
    assert (tmp_path / "wire_raw.log").read_bytes() == wire + b"unfinished tail"


@pytest.fixture
def serial_module(monkeypatch):
    class Serial(FakeSerial):
        chunks_to_read = [META + SWING + PPS]
        terminal_error = KeyboardInterrupt
        instances = []

        def __init__(self, *args, **kwargs):
            super().__init__(self.chunks_to_read, self.terminal_error)
            self.args, self.kwargs = args, kwargs
            self.closed = False
            self.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

    ports = [port("COM7", "Arduino Nano Every")]
    serial_tools = SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: ports))
    module = SimpleNamespace(Serial=Serial, SerialException=OSError, tools=serial_tools)
    monkeypatch.setitem(sys.modules, "serial", module)
    monkeypatch.setitem(sys.modules, "serial.tools", serial_tools)
    return module


def test_cli_requires_output_directory_before_opening_serial(serial_module):
    with pytest.raises(SystemExit) as error:
        nano.main([])
    assert error.value.code == 2
    assert serial_module.Serial.instances == []


def test_cli_lists_ports_without_opening_them_or_creating_files(serial_module, capsys, tmp_path):
    out = tmp_path / "unused"
    assert nano.main(["--list-ports", "--out", str(out)]) == 0
    assert "COM7" in capsys.readouterr().out
    assert serial_module.Serial.instances == []
    assert not out.exists()


def test_cli_ctrl_c_flushes_closes_and_reports_capture(serial_module, capsys, tmp_path):
    assert nano.main(["--out", str(tmp_path)]) == 0
    serial = serial_module.Serial.instances[0]
    assert serial.closed
    assert "COM7" in serial.args or serial.kwargs.get("port") == "COM7"
    assert serial.kwargs["baudrate"] == 115200
    assert serial.kwargs["timeout"] == .2
    assert [data for _, data in serial.writes] == [b"emit meta\n"]
    assert len(rows(tmp_path / "PCSW.CSV")) == len(rows(tmp_path / "PCPS.CSV")) == 1
    assert "Captured:" in capsys.readouterr().out


def test_cli_explicit_port_baud_and_passive(serial_module, tmp_path):
    assert nano.main(["--out", str(tmp_path), "--port", "COM19", "--baud", "57600", "--passive"]) == 0
    serial = serial_module.Serial.instances[0]
    assert "COM19" in serial.args or serial.kwargs.get("port") == "COM19"
    assert serial.kwargs["baudrate"] == 57600
    assert serial.writes == []


def test_cli_collision_refusal_happens_before_serial_open(serial_module, tmp_path):
    (tmp_path / "PCSW.CSV").write_bytes(b"keep me")
    assert nano.main(["--out", str(tmp_path)]) != 0
    assert serial_module.Serial.instances == []
    assert (tmp_path / "PCSW.CSV").read_bytes() == b"keep me"


def test_cli_disconnect_closes_serial_and_returns_failure(serial_module, tmp_path, capsys):
    serial_module.Serial.terminal_error = OSError
    assert nano.main(["--out", str(tmp_path)]) != 0
    assert serial_module.Serial.instances[0].closed
    assert "failed" in capsys.readouterr().err.lower()
    assert (tmp_path / "wire_raw.log").read_bytes() == META + SWING + PPS
