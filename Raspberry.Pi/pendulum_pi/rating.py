"""Display rates derived from full-period estimates; never changes capture data."""
import math


def gain_seconds_per_day(period_s, target_period_s):
    """Positive is gaining. An unset target or invalid period has no rate."""
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (period_s, target_period_s)):
        return None
    return 86400.0 * (target_period_s / period_s - 1.0)


def rated_display(display, target_period_s):
    """Copy the estimator snapshot and attach rates without another smoothing pass."""
    result = dict(display, target_period_s=target_period_s)
    for name in ('window',):
        horizon = dict(display.get(name) or {})
        period = horizon.get('period_us')
        period_s = period / 1_000_000 if type(period) in (int, float) else None
        horizon['gain_seconds_per_day'] = (None if display.get('stale') or not display.get('available')
                                           else gain_seconds_per_day(period_s, target_period_s))
        result[name] = horizon
    result['rows'] = display_rows(result)
    return result


def display_rows(display):
    """One shared numerical snapshot; OLED presentation only rounds these values."""
    window = display.get('window') or {}
    if not display.get('available') or display.get('stale'):
        state = 'stale' if display.get('available') else 'waiting'
        return [f'{label}: {state}' for label in ('P', 'B', 'R', 'dO', 'dB', 'MEAN600s')]

    def row(label, value, places, unit, signed=False):
        if type(value) not in (int, float) or not math.isfinite(value):
            return f'{label}: --'
        for digits in range(places, -1, -1):
            rounded = 0.0 if abs(value) < .5 / 10 ** digits else value
            text = f'{label} {rounded:{"+" if signed else ""}.{digits}f}{unit}'
            if len(text) <= 21:
                return text
        return f'{label}: range'

    period = window.get('period_us')
    period_s = period / 1_000_000 if type(period) in (int, float) else None
    return [row('P', period_s, 9, 's'), row('B', window.get('bpm'), 6, 'BPM'),
            row('R', window.get('gain_seconds_per_day'), 6, 's/d', True),
            row('dO', window.get('tick_delta_us'), 2, 'us', True),
            row('dB', window.get('block_delta_us'), 2, 'us', True),
            'MEAN600s ' + ('restored' if window.get('priming', {}).get('active') else 'filling' if window.get('learning') else display.get('timebase', 'WAIT'))]
