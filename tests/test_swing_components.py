import json
import re
import numpy as np
import pandas as pd
import pytest
from scipy.integrate import trapezoid
from pendulum_analysis.swing_components import core
from pendulum_analysis.swing_components.distributions import layout_hints
from pendulum_analysis.swing_components.rendering import safe_json


def events(n=240,nominal=2.):
    seq=np.arange(n);t=nominal*seq
    return dict(sequence=seq,phase=seq%15,epoch=np.zeros(n,int),source_row=seq+2,
        valid=np.ones(n,bool),error_us=10*np.sin(seq*.31)+.02*seq,start_s=t,end_s=t+nominal,nominal_s=nominal)


@pytest.mark.parametrize('nominal',[2.,3.])
def test_rolling_means_ranges_and_local_sd_never_bridge_gap(nominal):
    e=events(nominal=nominal);e['valid'][70]=False
    dense,sparse,_=core.rolling(e)
    for s in [dense,sparse]:
        for i in np.flatnonzero(np.isfinite(s.range)):
            mask=(s.run==s.run[i])&(s.t>s.t[i]-120)&(s.t<=s.t[i])
            assert s.range[i]==pytest.approx(np.ptp(s.mean[mask]),abs=1e-10)
            assert s.sd[i]==pytest.approx(np.std(s.mean[mask]),abs=1e-10)
        for rid in np.unique(s.run):
            indices=np.flatnonzero(s.run==rid)
            assert np.isnan(s.range[indices][s.t[indices]-s.t[indices[0]]<120]).all()
    cycles=core.cycles(e)
    np.testing.assert_array_equal(sparse.seq,cycles['seq']+14)
    np.testing.assert_allclose(sparse.mean,cycles['mean'])
    for i,seq in enumerate(dense.seq):
        support=np.arange(seq-14,seq+1)
        assert e['valid'][support].all()
        assert dense.mean[i]==pytest.approx(e['error_us'][support].mean())


def test_known_spectral_frequency_keeps_physical_power():
    n=8192;t=np.arange(n)*30.;y=2*np.sin(2*np.pi*t/94.)
    spectra,_=core.spectra(dict(mean=y,seq=np.arange(n)*15,t=t,runs=[slice(0,n)]))
    for s in spectra:
        f,p=s['frequency_hz'],s['psd_us2_per_hz']
        assert abs(f[np.argmax(p)]-1/94.)<=1/(s['segment_cycles']*30.)
        assert trapezoid(p,f)==pytest.approx(2.,abs=.05)


def test_layout_hints_are_local_and_not_a_fixed_impulse_mask():
    a=np.random.default_rng(0).normal(0,20,(2000,15));a[:,6]+=3000;a[:,7]+=1500
    _,seeds,_=layout_hints(a);assert seeds==[6,7]
    _,seeds,_=layout_hints(np.random.default_rng(1).normal(0,500,(2000,15)))
    assert seeds==[]


def test_embedded_json_preserves_data_without_script_end_tag():
    value={'label':'</script><script>example</script>'}
    encoded=safe_json(value)
    assert '</script>' not in encoded
    assert json.loads(encoded)==value


def test_stability_matches_direct_block_differences_and_pca_recovers_rank_two():
    f=np.sin(np.arange(120)*.17)*2e-6+np.arange(120)*1e-9
    c=dict(mean=(2/(1+f)-2)*1e6,seq=np.arange(120)*15,t=np.arange(120)*30.,runs=[slice(0,120)])
    table,_=core.stability(c);actual=2/(2+c['mean']/1e6)-1
    for row in table.itertuples():
        m=row.averaging_cycles
        direct=np.array([actual[i:i+m].mean() for i in range(len(actual)-m+1)])
        adev=np.sqrt(np.mean((direct[m:]-direct[:-m])**2)/2)*1e6
        hdev=np.sqrt(np.mean((direct[2*m:]-2*direct[m:-m]+direct[:-2*m])**2)/6)*1e6
        np.testing.assert_allclose([row.adev_ppm,row.hdev_ppm],[adev,hdev],rtol=1e-7,atol=1e-10)
    scores=np.column_stack([np.sin(np.arange(500)*.11)*50,np.cos(np.arange(500)*.07)*10])
    loading=np.zeros((2,15));loading[0,6:8]=[1,1];loading[1,6:8]=[-1,1]
    a=scores@loading+np.arange(15)
    model=core.pca(dict(phases=a))
    assert model['fraction'][:2].sum()==pytest.approx(1)
    np.testing.assert_allclose(model['scores']@model['loadings'][:3]+model['phase_means_us'],a,atol=1e-10)
