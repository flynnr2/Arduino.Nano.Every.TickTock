"""Moist-air density, kg/m³, from °C, % RH and absolute local hPa.

CIPM-2007: Picard et al., Metrologia 45 (2008) 149–155,
https://doi.org/10.1088/0026-1394/45/2/004, eqs. (1), (4), Appendix A.
Constants intentionally retain the published 2007 values (including R).
Only math is imported, so the live Pi can use the same implementation.
"""
import math

MODEL = 'CIPM-2007'
CO2_PPM = 400.0  # Assumed, not measured; density is conditional on this value.
CLEAN_START = 1793538000.0  # 2026-11-01T13:00:00Z, operator declared.


def _inputs(t, rh, p):
    return all(type(v) in (int, float) and math.isfinite(v) for v in (t, rh, p))


def cipm2007(t, rh, p, co2_ppm=CO2_PPM):
    """Return density or None outside the paper's recommended validity range.

    15–27 °C, 600–1100 hPa, 0–100% RH. CO₂ is supplied in ppm, not fraction.
    No extrapolation, sea-level correction, or claim of calibrated accuracy.
    """
    if not _inputs(t, rh, p) or not (15 <= t <= 27 and 0 <= rh <= 100 and 600 <= p <= 1100):
        return None
    if type(co2_ppm) not in (int, float) or not math.isfinite(co2_ppm) or not 0 <= co2_ppm <= 1000:
        return None
    T, pressure = t + 273.15, p * 100.0
    saturation = math.exp(1.2378847e-5*T*T - 1.9121316e-2*T + 33.93711047 - 6.3431645e3/T)
    enhancement = 1.00062 + 3.14e-8*pressure + 5.6e-7*t*t
    xv = rh / 100.0 * enhancement * saturation / pressure
    Z = (1 - pressure/T * (1.58123e-6 - 2.9331e-8*t + 1.1043e-10*t*t
         + (5.707e-6 - 2.051e-8*t)*xv + (1.9898e-4 - 2.376e-6*t)*xv*xv)
         + (pressure/T)**2 * (1.83e-11 - 0.765e-8*xv*xv))
    Ma = (28.96546 + 12.011*(co2_ppm*1e-6 - 0.0004))*1e-3
    return pressure*Ma/(Z*8.314472*T) * (1 - xv*(1 - 18.01528e-3/Ma))


def legacy_density(t, rh, p):
    """Previous ideal moist mixture + Buck saturation calculation, unchanged."""
    if not _inputs(t, rh, p) or not (-80 <= t <= 85 and 0 <= rh <= 100 and 300 <= p <= 1100):
        return None
    e = rh/100 * 611.21 * math.exp((18.678-t/234.5)*t/(257.14+t))
    return (p*100-e)/(287.05*(t+273.15)) + e/(461.5*(t+273.15))


def density_values(t, rh, p):
    """JSON-safe paired values and their signed difference."""
    density = cipm2007(t, rh, p)
    legacy = legacy_density(t, rh, p)
    delta = density-legacy if density is not None and legacy is not None else None
    return dict(density_kg_m3=density, density_legacy_kg_m3=legacy,
                density_delta_kg_m3=delta,
                density_delta_ppm=delta/legacy*1e6 if delta is not None else None)


def density_array(t, rh, p, *, legacy=False):
    """Broadcast array adapter for offline analysis; invalid values become NaN."""
    import numpy as np
    arrays = np.broadcast_arrays(t, rh, p)
    calculate = legacy_density if legacy else cipm2007
    result = np.fromiter((float('nan') if (v := calculate(float(a), float(b), float(c))) is None else v
                          for a, b, c in zip(*(x.flat for x in arrays))), dtype=float, count=arrays[0].size)
    return result.reshape(arrays[0].shape)
