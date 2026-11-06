"""Density/window alignment, clean boundary and independent model fits."""
import math
import random
import pytest
from pendulum_analysis.air_density import cipm2007, legacy_density, CLEAN_START
from pendulum_pi.environmental import _TrailingEnvironment, environmental_fit, query_environmental_relationships
from pendulum_pi.history import _connect, DATABASE
from test_environmental import add, BASE


def test_density_calculated_before_window_averaging_and_no_future_values():
    window=_TrailingEnvironment()
    values=[]
    for second in range(0,601,10):
        t=18+6*second/600;values.append(cipm2007(t,60,1000))
        window.add(dict(time=BASE+second,temperature_C=t,humidity_pct=60,pressure_hPa=1000),None,660)
    mean=window.mean(BASE+600,600)
    assert mean['density_kg_m3']==pytest.approx(sum(values)/len(values))
    assert abs(mean['density_kg_m3']-cipm2007(mean['temperature_C'],60,1000))>1e-5
    window.add(dict(time=BASE+610,temperature_C=30,humidity_pct=60,pressure_hPa=1000),None,660)
    mean=window.mean(BASE+610,600)
    assert mean['temperature_C'] is not None and mean['density_kg_m3'] is None


@pytest.mark.parametrize('variables',[('density_kg_m3',),('temperature_C','density_kg_m3')])
def test_density_models_against_independent_numpy_fit_and_covariance(variables):
    np=pytest.importorskip('numpy');rng=random.Random(21);points=[]
    for i in range(480):
        t=20+math.sin(i/43);h=60+4*math.sin(i/31);p=1010+4*math.cos(i/27)
        density=cipm2007(t,h,p)
        period=2+(900*(density-1.2)+(2*(t-20) if len(variables)==2 else 0)+rng.gauss(0,.1))*1e-6
        points.append(dict(time=BASE+60*i,bucket_start=BASE+60*i,samples=6,period_s=period,temperature_C=t,density_kg_m3=density))
    _,fit=environmental_fit(points,bucket_seconds=60,variables=variables)
    x=np.array([[p[k] for k in variables] for p in points]);x-=x.mean(axis=0)
    X=np.column_stack((np.ones(len(x)),x));y=np.array([p['period_s'] for p in points]);y=(y-y.mean())*1e6
    beta=np.linalg.lstsq(X,y,rcond=None)[0];residual=y-X@beta
    assert fit['coefficients_available']
    assert [fit['coefficients'][k]['slope_us_per_unit'] for k in variables]==pytest.approx(beta[1:],rel=1e-8)
    assert fit['residual_rms_us']==pytest.approx(np.sqrt(np.mean(residual**2)),rel=1e-8)
    bread=np.linalg.inv(X.T@X);scores=X*residual[:,None];meat=scores.T@scores
    lags=fit['uncertainty']['lag_buckets']
    for lag in range(1,lags+1):
        cross=scores[lag:].T@scores[:-lag];meat+=(1-lag/(lags+1))*(cross+cross.T)
    covariance=bread@meat@bread*len(x)/(len(x)-len(variables)-1)
    assert [fit['coefficients'][k]['se_us_per_unit'] for k in variables]==pytest.approx(np.sqrt(np.diag(covariance))[1:],rel=1e-7)


def test_clean_boundary_does_not_borrow_earlier_swing_window(tmp_path):
    import json
    connection=_connect(tmp_path/DATABASE)
    payload=json.dumps(dict(quality=dict(window_learning=False,timebase='PPS'),settings=dict(window_swings=300)))
    for second in range(-600,1201,10):
        connection.execute('INSERT INTO observations(time,session,segment,source,payload,temperature_C,humidity_pct,pressure_hPa,window_period_s) VALUES(?,?,?,?,?,?,?,?,?)',
            (CLEAN_START+second,'s','a','serial',payload,20+second/10000,50.,1000.,2.))
    connection.commit();connection.close()
    result=query_environmental_relationships(tmp_path,CLEAN_START,CLEAN_START+1200)
    group=result['segments'][0]
    assert group['points'][0]['time']>=CLEAN_START+600
    assert len({len(m['points']) for m in group['density_models'].values()})==1
    assert group['density_comparison']['averages']==len(group['points'])
    assert all(p['density_kg_m3']>p['density_legacy_kg_m3'] for p in group['points'])
