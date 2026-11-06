import numpy as np
import pandas as pd
import pytest
from pendulum_analysis.suite.common import ENV,Settings
from pendulum_analysis.suite.environment_models import fit_environment,JOINT
from pendulum_analysis.suite.swing_environment_fits import swing_environment_fits
from pendulum_analysis.suite.time_series import cycle_series
from test_swing_time_series import frame


def population(x,y):
    n=len(y)
    joint=pd.DataFrame(dict(epoch=0,hour=np.arange(n),samples=120,response=y,eligible=True,
        start_s=np.arange(n)*3600,end_s=(np.arange(n)+1)*3600,**dict(zip(ENV,x.T))))
    long=pd.concat([joint[['epoch','hour','samples','response','eligible','start_s','end_s',c]].rename(columns={c:'reading'}).assign(channel=c) for c in ENV])
    return long,joint


def test_all_three_slopes_and_future_holdout():
    x=np.random.default_rng(8).normal(size=(100,3));y=7+x@np.array([2.,-3.,4.])
    models,validation,pred=fit_environment(*population(x,y),[0])
    joint=models[models.channel.eq(JOINT)].iloc[0]
    assert joint.adjusted_slopes_available
    assert [joint[c+'_slope'] for c in ENV]==pytest.approx([2,-3,4])
    assert joint.r_squared==pytest.approx(1)
    check=validation[validation.channel.eq(JOINT)].iloc[0]
    assert check.train_hours==70 and check.test_hours==30
    assert check.model_rmse<1e-12
    assert set(models.channel)==set((*ENV,JOINT))
    np.testing.assert_allclose(pred[pred.channel.eq(JOINT)].observed,pred[pred.channel.eq(JOINT)].fitted,atol=1e-12)


def test_nonseparable_predictors_keep_prediction_but_withhold_slopes():
    t=np.arange(80.);x=np.column_stack([t,3*t,np.sin(t)]);y=4+2*t-np.sin(t)
    models,_,pred=fit_environment(*population(x,y),[0])
    joint=models[models.channel.eq(JOINT)].iloc[0]
    assert joint.predictor_rank==2
    assert not joint.adjusted_slopes_available
    assert all(np.isnan(joint[c+'_slope']) for c in ENV)
    assert 'withheld' in joint.status
    p=pred[pred.channel.eq(JOINT)]
    np.testing.assert_allclose(p.observed,p.fitted,atol=1e-10)


def test_joint_fit_uses_same_complete_cycles_for_every_sensor():
    f=frame(np.arange(3600),np.full(3600,2.0003))
    for c,(a,b) in zip(ENV,[(20,21),(50,51),(1000,1001)]):f[c]=np.linspace(a,b,len(f))
    for index,c in zip([0,20,35],ENV):f.loc[index,c]=np.nan
    blocks,_,_=cycle_series(f,Settings())
    tables=swing_environment_fits(f,blocks,Settings())
    assert tables['swing_environment_joint_hourly'].samples.tolist()==[117,120]
    for c in ENV:
        assert tables['swing_environment_hourly'].loc[lambda x:x.channel.eq(c),'cycles'].tolist()==[119,120]


def test_joint_future_changes_do_not_leak_into_training():
    x=np.random.default_rng(12).normal(size=(100,3));y=x@np.array([2.,-3.,4.]);y[70:]+=100
    _,check,_=fit_environment(*population(x,y),[0])
    assert check.loc[check.channel.eq(JOINT),'model_rmse'].iloc[0]==pytest.approx(100)


def test_density_regressors_share_population_and_keep_training_chronological():
    from pendulum_analysis.air_density import density_array
    n=100;t=np.arange(n,dtype=float)
    x=np.column_stack([20+np.sin(t/13),55+3*np.cos(t/17),1010+5*np.sin(t/8)])
    density=density_array(*x.T)
    y=3*x[:,0]+900*density
    long,joint=population(x,y)
    joint['density_kg_m3']=density
    joint['density_legacy_kg_m3']=density_array(*x.T,legacy=True)
    joint.loc[2,'density_kg_m3']=np.nan
    models,validation,pred=fit_environment(long,joint,[0])
    selected=models.loc[models.channel.isin(['density','legacy_density','temperature_density','temperature_legacy_density','density_matched_sensors'])]
    assert selected.hours.tolist()==[99]*5
    fitted=selected.loc[selected.channel.eq('temperature_density')].iloc[0]
    assert fitted.temperature_C_slope==pytest.approx(3,abs=1e-10)
    assert fitted.density_kg_m3_slope==pytest.approx(900,abs=1e-8)
    assert validation.loc[validation.channel.eq('temperature_density'),'model_rmse'].iloc[0]<1e-10
    # A changed response in the held-out future must not affect training.
    joint.loc[70:,'response']+=100
    _,validation,_=fit_environment(long,joint,[0])
    assert validation.loc[validation.channel.eq('temperature_density'),'model_rmse'].iloc[0]==pytest.approx(100)
