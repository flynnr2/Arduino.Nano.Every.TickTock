"""Verify actual SSD1306 command/data bytes and display semantics."""
from types import SimpleNamespace

import pytest

from pendulum_pi.oled import ChangedPagesOLED, OledView, oled_lines
from pendulum_pi.peripherals import render_oled
from pendulum_pi.common import atomic_json
from pendulum_pi.config import Settings
from pendulum_pi.results import saved_status


class Device:
    def __init__(self):
        self.writes = []
        self.fail = False

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def write(self, data):
        if self.fail:
            raise OSError('OLED disconnected')
        self.writes.append(bytes(data))


def display():
    device = Device()
    driver = SimpleNamespace(buffer=bytearray([0x40] + [0] * 1024), i2c_device=device)
    return ChangedPagesOLED(driver), device, driver


def test_first_frame_sends_eight_pages_then_unchanged_frame_sends_nothing():
    oled, device, _ = display()
    oled.show()
    assert len(device.writes) == 16
    for page in range(8):
        assert device.writes[2 * page] == bytes([0, 0x21, 0, 127, 0x22, page, page])
        assert device.writes[2 * page + 1] == b'\x40' + b'\0' * 128
    assert oled.bytes_sent == 1024
    device.writes.clear()
    oled.show()
    assert device.writes == []


def test_only_changed_column_span_of_changed_page_is_written():
    oled, device, driver = display()
    oled.show()
    device.writes.clear()
    driver.buffer[1 + 3 * 128 + 9] = 0xff
    driver.buffer[1 + 3 * 128 + 11] = 0x80
    oled.show()
    assert device.writes == [bytes([0, 0x21, 9, 11, 0x22, 3, 3]), b'\x40\xff\x00\x80']
    driver.buffer[1 + 3 * 128 + 9] = 0
    device.writes.clear()
    oled.show()
    assert device.writes == [bytes([0, 0x21, 9, 9, 0x22, 3, 3]), b'\x40\x00']


def test_partial_failure_invalidates_every_page_before_retry():
    oled, device, driver = display()
    oled.show()
    driver.buffer[7] = 1
    device.fail = True
    with pytest.raises(OSError):
        oled.show()
    assert oled.previous is None
    device.fail = False
    device.writes.clear()
    oled.show()
    assert len(device.writes) == 16


def status(now=0):
    return dict(updated_monotonic=now, updated_utc='2026-09-28T12:34:21+00:00',
                time_health={'status': 'synchronized', 'fresh': True}, connected=True, ready=True,
                logging={'active': True}, environment={'temperature_C': 20, 'humidity_pct': 45, 'pressure_hPa': 1001,
                'sht4x': {'ok': True, 'fresh': True}, 'bmp280': {'ok': True, 'fresh': True}},
                display={'timebase': 'PPS', 'rows': ['P 2000000us', 'BPM 30.000000', 'GAIN 0.000s/d', 'dB 10us', 'MEAN 600s', 'LOCKED'],
                         'window': {'window_seconds': 600}, 'forecast': {'enabled': False, 'cycle_length': 0},
                         'pps': {'status': 'LOCKED', 'initialized': True, 'last_accepted_monotonic': now - 1, 'record_age_seconds': 1, 'holdover_age_ms': 0, 'gps_status': 2,
                                 'fast_hz': 16000029.347, 'slow_hz': 16000029.347, 'blended_hz': 16000029.347}})


def test_uno_information_and_rotation_with_fractional_timebase():
    data = status()
    lines = oled_lines(data, 0, 0)
    assert lines[0] == '2026-09-28 12:34:21Z'
    assert lines[1:7] == data['display']['rows']
    assert lines[7] == 'STATUS: OK' and not lines.invert_header
    lines = oled_lines(status(30), 30, 0)
    assert lines[1:7] == ['T:20.0C RH:45.0%', 'P:1001.0hPa', 'GPS:LCK AGE:1s',
                          'TIMEBASE:PPS', 'LOG:ON SD:OK S:R B:R', 'MEAN 600s']
    lines = oled_lines(status(45), 45, 0)
    assert lines[1:7] == ['TIMEBASE Hz', 'EST   16000029.347000', '20s   16000029.347000',
                          '1h    16000029.347000', '', 'PPS LOCKED']


def test_uninitialized_timebase_has_placeholders_and_stale_estimate_is_labelled():
    data = status(45)
    data['display']['pps']['initialized'] = False
    lines = oled_lines(data, 45, 0)
    assert lines[2:5] == ['EST                --', '20s                --', '1h                 --']
    data['display']['pps'].update(initialized=True, status='STALE', record_age_seconds=6)
    lines = oled_lines(data, 45, 0)
    assert lines[6] == 'PPS STALE' and lines.invert_header


def test_cached_utc_is_never_presented_as_synchronized_without_fresh_health():
    data = status()
    data['time_health']['fresh'] = False
    assert oled_lines(data, 0, 0)[0] == 'UTC: waiting for sync'


def test_faults_rotate_and_header_is_highlighted():
    data = status()
    data['connected'] = False
    data['logging']['active'] = False
    view = OledView()
    first = view.lines(data, 0, 0)
    second = view.lines(data, 2, 0)
    assert first[0] == '! SERIAL ERROR' and second[0] == '! SD LOG OFF'
    assert first.invert_header and len(first[7]) <= 21
    pytest.importorskip('PIL')
    assert render_oled(first).size == (128, 64)


def test_body_throttle_transitions_and_configuration_changes():
    view = OledView()
    data = status()
    first = view.lines(data, 0, 0)
    data['display']['rows'][0] = 'changed'
    assert view.lines(data, 1, 0)[1] == first[1]
    assert view.lines(data, 2, 0)[1] == 'changed'
    data['updated_monotonic'] = 30
    assert view.lines(data, 30, 0)[1] == 'T:20.0C RH:45.0%'
    data['environment']['temperature_C'] = 21
    assert view.lines(data, 31, 0)[1] == 'T:20.0C RH:45.0%'
    data['display']['forecast'].update(enabled=True, cycle_length=15)
    assert view.lines(data, 31, 0)[6] == 'MEAN 600s FC:15'
    data['updated_monotonic'] = 45
    assert view.lines(data, 45, 0)[1] == 'TIMEBASE Hz'


def test_drop_warning_only_on_increase_expires_and_ignores_counter_reset():
    view = OledView()
    data = status()
    data['counters'] = {'swing_missing': 100}
    assert view.lines(data, 0, 0)[7] == 'STATUS: OK'
    data['counters']['swing_missing'] = 101
    assert view.lines(data, 1, 0)[7] == '! NANO DROPS RISING'
    data['updated_monotonic'] = 13
    assert view.lines(data, 13, 0)[7] == 'STATUS: OK'
    data['counters']['swing_missing'] = 0
    assert view.lines(data, 14, 0)[7] == 'STATUS: OK'


def test_ticker_uses_whole_windows_and_inverted_background():
    view = OledView()
    data = status()
    message = '123456789012345678901abcdefghijklmnop'
    view.scroll_log(message, 0)
    assert view.lines(data, 0, 0)[7] == message[:21]
    assert view.lines(data, 3, 0)[7] == message[:21]
    data['updated_monotonic'] = 4
    lines = view.lines(data, 4, 0)
    assert lines[7] == message[21:42]
    pytest.importorskip('PIL')
    image = render_oled(lines)
    assert image.getpixel((127, 63)) == 1  # Whole last row is inverse, including margin.
    data['updated_monotonic'] = 12
    assert view.lines(data, 12, 0)[7] == 'STATUS: OK'


def test_fault_rotation_restarts_at_first_fault_when_fault_set_changes():
    view = OledView()
    data = status(7)
    data['connected'] = False
    data['logging']['active'] = False
    assert view.lines(data, 7, 0)[0] == '! SERIAL ERROR'
    assert view.lines(data, 9, 0)[0] == '! SD LOG OFF'
    data['logging']['active'] = True
    assert view.lines(data, 10, 0)[0] == '! SERIAL ERROR'


@pytest.mark.parametrize('now,body', [(0, 'P 2000000us'), (29, 'P 2000000us'),
                                    (30, 'T:20.0C RH:45.0%'), (44, 'T:20.0C RH:45.0%'),
                                    (45, 'TIMEBASE Hz'), (59, 'TIMEBASE Hz'),
                                    (60, 'P 2000000us'), (120, 'P 2000000us')])
def test_rotation_boundaries_match_uno(now, body):
    assert oled_lines(status(now), now, 0)[1] == body


def test_supplemental_holdover_and_stale_gps_formats_match_uno():
    data = status(30)
    data['display']['pps'].update(gps_status=3, holdover_age_ms=4500)
    assert oled_lines(data, 30, 0)[3] == 'GPS:HLD HAG:4s'
    data['display']['pps']['record_age_seconds'] = 6
    assert oled_lines(data, 30, 0)[3] == 'GPS:STL AGE:6s'


def test_uno_font_geometry_and_timebase_full_width_fit():
    from pendulum_pi.font5x7 import FONT
    assert len(FONT) == 95 * 5
    pytest.importorskip('PIL')
    image = render_oled(['A'])
    assert [sum((1 << y) for y in range(8) if image.getpixel((x, y))) for x in range(5)] == [0x7c, 0x12, 0x11, 0x12, 0x7c]
    data = status(45)
    data['display']['pps']['blended_hz'] = 4294967295.0
    line = oled_lines(data, 45, 0)[2]
    assert line == 'EST 4294967295.000000' and len(line) == 21
    image = render_oled([line])
    assert all(image.getpixel((x, y)) == 0 for x in (126, 127) for y in range(8))


def test_periodic_ack_check_and_full_refresh_match_uno_cadence():
    clock = [0]
    oled, device, driver = display()
    oled = ChangedPagesOLED(driver, clock=lambda: clock[0])
    oled.show()
    device.writes.clear()
    clock[0] = 29
    oled.show()
    assert device.writes == []
    clock[0] = 30
    oled.show()
    assert device.writes == [b'\x00\xe3']
    device.writes.clear()
    clock[0] = 60
    oled.show()
    assert device.writes[0] == b'\x00\xe3'
    assert len(device.writes) == 17 and oled.pages_sent == 16


def test_utc_header_holds_between_15_second_slots():
    view, data = OledView(), status()
    assert view.lines(data, 0, 0)[0] == '2026-09-28 12:34:21Z'
    data.update(updated_monotonic=4, updated_utc='2026-09-28T12:34:25+00:00')
    assert view.lines(data, 4, 0)[0] == '2026-09-28 12:34:21Z'
    data.update(updated_monotonic=9, updated_utc='2026-09-28T12:34:30+00:00')
    assert view.lines(data, 9, 0)[0] == '2026-09-28 12:34:30Z'


def test_feed_fault_thresholds_and_priority_match_uno():
    data = status(3)
    data['display'].update(last_swing_monotonic=0, stale=False)
    data['display']['pps']['record_age_seconds'] = 3
    view = OledView()
    assert view.lines(data, 3, 0)[0] == '! SWING FEED STALE'
    assert view.lines(data, 5, 0)[0] == '! PPS FEED STALE'
    assert '! PERIOD STALE' not in view.faults


@pytest.mark.parametrize('swing_at,pps_at,expected', [
    (19., 19., ()),
    (17., 19., ('! SWING FEED STALE',)),
    (19., 17., ('! PPS FEED STALE',)),
    (17., 17., ('! SWING FEED STALE', '! PPS FEED STALE')),
    (None, None, ()),
])
def test_saved_analysis_delay_does_not_age_received_feeds(tmp_path, swing_at, pps_at, expected):
    settings = Settings(runtime_dir=tmp_path)
    capture = status(20.)
    capture.update(boot_id='boot', last_swing_received_monotonic=swing_at,
                   last_pps_received_monotonic=pps_at)
    capture['logging']['session'] = 'recording'
    atomic_json(tmp_path/'status.json', capture)
    display = capture['display']
    display['last_swing_monotonic'] = 12.
    display['pps']['record_age_seconds'] = 7.
    atomic_json(tmp_path/'analysis.json', dict(published_monotonic=20., result=dict(
        boot_id='boot', logging={'session': 'recording'}, updated_monotonic=15., display=display)))
    combined = saved_status(settings, now=20.)
    view = OledView()
    view.lines(combined, 20., 0.)
    assert view.faults == expected
    # A genuinely late analysis result still has its separate period warning.
    combined = saved_status(settings, now=25.)
    combined.update(updated_monotonic=25., last_swing_received_monotonic=25.,
                    last_pps_received_monotonic=25.)
    view.lines(combined, 25., 0.)
    assert view.faults == ('! PERIOD STALE',)


def test_new_diagnostic_errors_enqueue_once_and_expire():
    view, data = OledView(), status()
    data['logging']['error'] = 'Cannot write recording segment'
    view.lines(data, 0, 0)
    assert view.alerts == [('Cannot write recording segment', 12)]
    data['updated_monotonic'] = 4
    view.lines(data, 4, 0)
    assert view.alerts == [('Cannot write recording segment', 12)]
    data['updated_monotonic'] = 12
    view.lines(data, 12, 0)
    assert view.alerts == []
