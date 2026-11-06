"""Numerical reference, validity, broadcasting and moist-air behaviour."""
import math
import numpy as np
import pytest
from pendulum_analysis.air_density import cipm2007, legacy_density, density_array, density_values, CLEAN_START


def test_reference_values_and_original_equation():
    # Independent full-precision evaluation of Picard eq. (1), Appendix A;
    # benchmark at p=101325 Pa, t=20 °C, h=.5, xCO2=.0004.
    assert cipm2007(20,50,1013.25) == pytest.approx(1.1993138954744933, abs=2e-13)
    assert cipm2007(20,0,1013.25) == pytest.approx(1.2045573416280522, abs=2e-13)
    assert legacy_density(20,50,1013.25) == pytest.approx(1.198866258805397, abs=2e-13)
    assert cipm2007(20,100,1013.25) < cipm2007(20,50,1013.25) < cipm2007(20,0,1013.25)
    assert cipm2007(20,50,1013.25,800) > cipm2007(20,50,1013.25,400)
    assert cipm2007(21,50,1013.25) < cipm2007(20,50,1013.25)
    assert cipm2007(20,50,1020) > cipm2007(20,50,1013.25)


@pytest.mark.parametrize('values',[(14.99,50,1000),(27.01,50,1000),(20,-1,1000),(20,101,1000),(20,50,599),(20,50,1101),(None,50,1000),(True,50,1000),(math.nan,50,1000),(20,math.inf,1000)])
def test_unavailable_outside_recommended_range(values):
    assert cipm2007(*values) is None
    assert density_values(*values)['density_delta_ppm'] is None


def test_boundary_units_and_array_adapter():
    assert cipm2007(15,0,600) is not None
    assert cipm2007(27,100,1100) is not None
    assert cipm2007(20,50,101325) is None  # Pa must not silently pass as hPa.
    actual=density_array([[20],[21]], [0,50,100],1013.25)
    assert actual.shape==(2,3)
    assert actual[0,1]==cipm2007(20,50,1013.25)
    assert np.isnan(density_array([14,20],50,1000)[0])
    from datetime import datetime,timezone
    assert CLEAN_START==datetime(2026,11,1,13,tzinfo=timezone.utc).timestamp()
