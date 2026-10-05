"""Regression checks for restored phase distributions and paired cancellation."""
import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.suite import report


@pytest.mark.parametrize("metric,bins,medians,references", [
    ("full",15,[1.999,2.003],[2.,2.]),
    ("half",30,[.991,1.009],[.99,1.01]),
])
def test_median_only_polars_use_correct_pooled_references(tmp_path, monkeypatch, metric, bins, medians, references):
    table = pd.DataFrame(dict(epoch=0, basis="pps_calibrated", metric=metric, bin_count=bins,
                              phase=np.arange(bins), count=[10,10]+[0]*(bins-2),
                              median=medians+[np.nan]*(bins-2), mean=[9.,9.]+[np.nan]*(bins-2)))
    totals = pd.DataFrame(dict(epoch=0, basis="pps_calibrated",
                               metric=["full"] if bins == 15 else ["tick_half","tock_half"],
                               median=[2.] if bins == 15 else [.99,1.01]))
    captured = []
    def inspect(fig, path):
        assert len(fig.axes) == 1
        ax = fig.axes[0]
        captured.extend(bar.get_height() for bar in ax.patches)
        assert ax.get_title() == "Median (µs)"
        report.plt.close(fig)
    monkeypatch.setattr(report, "finish", inspect)
    assert report.polar_pair(table,totals,metric,bins,"pps_calibrated",0,False,
                             tmp_path/"median.png",median_only=True)
    assert captured == pytest.approx(np.abs(np.asarray(medians)-references)*1e6)


def frame():
    data=pd.DataFrame(dict(epoch=0,phase15=[0,0,0,2,2,2],raw_valid=True,
                          calibrated_valid=[True,True,False,True,True,False],
                          tick_half_s=[.98,.99,1.,1.,1.01,1.02]))
    data['tock_half_s']=2-data.tick_half_s
    data['full_s']=data.tick_half_s+data.tock_half_s
    for metric in ('full','tick_half','tock_half'):
        data[metric+'_pps_s']=data[metric+'_s']*1.0001
    return data


def test_boxplots_keep_event_phase_ownership_and_eligibility(tmp_path,monkeypatch):
    from matplotlib.axes import Axes
    before=frame()
    data=before.copy(deep=True)
    calls=[]
    original=Axes.boxplot
    def capture(self,samples,*args,**kwargs):
        calls.append([np.asarray(a).copy() for a in samples])
        return original(self,samples,*args,**kwargs)
    monkeypatch.setattr(Axes,'boxplot',capture)
    plots=report.plot_swing_boxes(data,tmp_path)
    assert len(plots)==4
    assert [len(call) for call in calls]==[15,30,15,30]
    np.testing.assert_allclose(calls[1][0],[980,990,1000])
    np.testing.assert_allclose(calls[1][1],[1020,1010,1000])
    assert not len(calls[1][2]) and not len(calls[1][3])
    np.testing.assert_allclose(calls[1][4],[1000,1010,1020])
    assert len(calls[2][0])==2 and len(calls[2][2])==2
    assert all((tmp_path/name).stat().st_size>0 for name,_,_ in plots)
    pd.testing.assert_frame_equal(data,before)


def test_cancellation_pairs_not_independently_sorted_or_pooled(tmp_path):
    data=frame()
    # A second epoch with constant, asymmetric halves must stay separate.
    other=data.iloc[:2].copy()
    other['epoch']=1
    other['tick_half_s']=.9
    other['tock_half_s']=1.1
    other['calibrated_valid']=False
    data=pd.concat([data,other],ignore_index=True)
    # A rejected extreme pair must not affect any statistics.
    rejected=data.iloc[[0]].copy()
    rejected['raw_valid']=rejected['calibrated_valid']=False
    rejected['tick_half_s']=100
    rejected['tock_half_s']=300
    data=pd.concat([data,rejected],ignore_index=True)
    before=data.copy(deep=True)
    plots=report.plot_half_cancellation(data,tmp_path)
    saved=pd.read_csv(tmp_path/'half_cancellation_statistics.csv')
    assert len(plots)==3 and len(saved)==6
    raw=saved[(saved.epoch==0)&(saved.basis=='raw')]
    assert raw['count'].tolist()==[6,6]
    assert raw.correlation.tolist()==pytest.approx([-1,-1])
    assert raw.full_std_ms.tolist()==pytest.approx([0,0],abs=1e-10)
    assert raw.independent_full_std_ms.gt(0).all()
    whole=raw[raw.centring=='epoch_mean'].iloc[0]
    within=raw[raw.centring=='phase_mean'].iloc[0]
    assert whole.tick_std_ms==pytest.approx(np.std(frame().tick_half_s*1000,ddof=1))
    assert within.tick_std_ms<whole.tick_std_ms
    assert saved.loc[saved.basis=='pps_calibrated','count'].tolist()==[4,4]
    constant=saved[saved.epoch==1]
    assert constant.correlation.isna().all()
    assert constant.full_std_ms.eq(0).all()
    pd.testing.assert_frame_equal(data,before)
    assert all((tmp_path/name).stat().st_size>0 for name,_,_ in plots)


@pytest.mark.parametrize('population',['empty','excluded','singleton','nonfinite'])
def test_unavailable_pairs_produce_readable_empty_export(tmp_path,population):
    data=frame()
    if population=='empty':data=data.iloc[:0]
    elif population=='excluded':data['raw_valid']=data['calibrated_valid']=False
    elif population=='singleton':data=data.iloc[:1]
    else:
        data['tick_half_s']=np.nan
        data['tick_half_pps_s']=np.inf
    assert report.plot_half_cancellation(data,tmp_path)==[]
    saved=pd.read_csv(tmp_path/'half_cancellation_statistics.csv')
    assert saved.empty
    assert list(saved.columns)==['epoch','basis','centring','count','correlation',
                                 'tick_std_ms','tock_std_ms','full_std_ms','independent_full_std_ms']
    if population in ('empty','excluded'):
        assert report.plot_swing_boxes(data,tmp_path)==[]


def test_complete_report_preserves_old_outputs_and_links_new_diagnostics(tmp_path):
    # Use the established metrology fixtures, including hardware timer wraps.
    from test_suite import clock_frame,swings_frame
    from pendulum_analysis.suite.__main__ import run
    from pendulum_analysis.suite.common import Settings
    clock_frame(400).drop(columns='source_row').to_csv(tmp_path/'PCPS.CSV',index=False)
    swings_frame(np.arange(150)).drop(columns='source_row').to_csv(tmp_path/'PCSW.CSV',index=False)
    output=tmp_path/'report'
    result=run(tmp_path,output,Settings(diagnostics=True),progress=lambda _:None)
    expected={f'swing_e0_{basis}_{metric}_boxplot.png'
              for basis in ('raw','pps_calibrated') for metric in ('full','half')}
    expected|={f'swing_e0_{basis}_half_cancellation.png' for basis in ('raw','pps_calibrated')}
    assert expected<=set(result['plots'])
    assert {'swing_period.png','swing_phase_exposure.png','clock_environment.png',
            'swing_e0_pps_calibrated_full_mean_median.png'}<=set(result['plots'])
    html=(output/'report.html').read_text()
    markdown=(output/'report.md').read_text()
    assert html.count('data:image/png;base64,')==len(result['plots'])
    for text in (html,markdown):
        assert 'plots/half_cancellation_statistics.csv' in text
        assert 'csv/swing_component_summary.csv' in text
        assert 'box-and-whisker' in text and 'tick / tock cancellation' in text
    assert (output/'csv'/'swing_phase.csv').is_file()
    assert len(pd.read_csv(output/'plots'/'half_cancellation_statistics.csv'))==4
