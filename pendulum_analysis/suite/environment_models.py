"""Matched hourly associations for all sensors and their shared-population fit."""
import numpy as np
import pandas as pd
from .common import ENV

JOINT = "all_three"
DENSITY_SPECS = {
    'density': ('density_kg_m3',),
    'temperature_density': ('temperature_C', 'density_kg_m3'),
    'legacy_density': ('density_legacy_kg_m3',),
    'temperature_legacy_density': ('temperature_C', 'density_legacy_kg_m3'),
    'density_matched_sensors': tuple(ENV),
}


def _solve(x, y):
    centre, reference = x.mean(axis=0), float(y.mean())
    scale = x.std(axis=0)
    z = (x-centre)/np.where(scale>0,scale,1.)
    coef, _, rank, singular = np.linalg.lstsq(z,y-reference,rcond=1e-10)
    condition = float(singular[0]/singular[-1]) if rank==x.shape[1] and singular[-1]>0 else np.inf
    separable = rank==x.shape[1] and condition<=30.
    return dict(centre=centre,reference=reference,scale=np.where(scale>0,scale,1.),
                coef=coef,rank=int(rank),condition=condition,separable=separable,
                prediction=reference+z@coef)


def fit_environment(long, joint, epochs, min_hours=24):
    """Descriptive OLS; earlier 70% train/later 30% test, with matched baselines.

    Single-sensor fits keep that sensor's own matched population. The joint fit
    must be supplied observations measured on an identical all-three population.
    Adjusted slopes are withheld for rank deficiency or condition number >30,
    following the dashboard's identifiability policy; fitted response still exists.
    """
    models, validation, predictions = [], [], []
    for epoch in epochs:
        extra = DENSITY_SPECS if all(k in joint for k in ('density_kg_m3', 'density_legacy_kg_m3')) else {}
        for channel in (*ENV, JOINT, *extra):
            derived = channel in extra
            columns=list(extra[channel]) if derived else list(ENV) if channel==JOINT else ["reading"]
            source=joint.loc[joint.epoch.eq(epoch)].copy() if derived or channel==JOINT else long.loc[long.epoch.eq(epoch)&long.channel.eq(channel)].copy()
            if derived:
                source = source.loc[np.isfinite(source[['density_kg_m3','density_legacy_kg_m3']]).all(axis=1)].copy()
                if len(columns) == 1:
                    source['reading'] = source[columns[0]]
            scalar = len(columns) == 1
            if len(source):
                finite=np.isfinite(source[[*columns,"response"]]).all(axis=1)
                source=source.loc[source.eligible&finite].sort_values("hour")
            n=len(source)
            row=dict(epoch=int(epoch),channel=channel,hours=n,samples=int(source.samples.sum()) if n else 0,
                start_day=float(source.start_s.min()/86400) if n else np.nan,
                end_day=float(source.end_s.max()/86400) if n else np.nan,
                sensor_min=float(source.reading.min()) if n and scalar else np.nan,
                sensor_max=float(source.reading.max()) if n and scalar else np.nan,
                slope_per_unit=np.nan,r_squared=np.nan,residual_rmse=np.nan,
                predictor_rank=0,condition_number=np.nan,adjusted_slopes_available=False,
                status="insufficient hourly coverage",**{c+"_slope":np.nan for c in (*ENV, 'density_kg_m3', 'density_legacy_kg_m3')})
            check=dict(epoch=int(epoch),channel=channel,train_hours=0,test_hours=0,split_hour=np.nan,
                model_rmse=np.nan,constant_rmse=np.nan,improvement_fraction=np.nan,
                status="insufficient holdout coverage")
            if n>=min_hours:
                x=source[columns].to_numpy(float);y=source.response.to_numpy(float)
                fit=_solve(x,y)
                if fit['rank']==0:
                    row['status']=check['status']='constant sensor readings'
                else:
                    residual=y-fit['prediction'];ss=float(np.sum((y-y.mean())**2))
                    row.update(r_squared=1-float(residual@residual)/ss if ss>0 else np.nan,
                        residual_rmse=float(np.sqrt(np.mean(residual**2))),predictor_rank=fit['rank'],
                        condition_number=fit['condition'] if np.isfinite(fit['condition']) else np.nan,
                        adjusted_slopes_available=bool(fit['separable']),
                        status='available' if fit['separable'] else 'predictors not separable; adjusted slopes withheld')
                    if fit['separable']:
                        slopes=fit['coef']/fit['scale']
                        if not scalar:
                            row.update({c+"_slope":float(v) for c,v in zip(columns,slopes)})
                        else:
                            row['slope_per_unit']=float(slopes[0])
                            if derived:row[columns[0]+'_slope']=float(slopes[0])
                    p=source[["epoch","hour","start_s","end_s"]].copy()
                    p['channel']=channel;p['reading']=source.reading if scalar else np.nan
                    p['observed']=y;p['fitted']=fit['prediction'];p['residual']=residual
                    predictions.append(p)
                    split=int(n*.7)
                    if split>=min_hours and n-split>=12:
                        train=_solve(x[:split],y[:split])
                        predicted=train['reference']+(x[split:]-train['centre'])/train['scale']@train['coef']
                        rmse=float(np.sqrt(np.mean((y[split:]-predicted)**2)))
                        baseline=float(np.sqrt(np.mean((y[split:]-y[:split].mean())**2)))
                        check.update(train_hours=split,test_hours=n-split,split_hour=int(source.hour.iloc[split]),
                            model_rmse=rmse,constant_rmse=baseline,
                            improvement_fraction=1-rmse/baseline if baseline else np.nan,
                            status='available' if train['rank'] else 'constant training readings')
            models.append(row);validation.append(check)
    pred=pd.concat(predictions,ignore_index=True) if predictions else pd.DataFrame(
        columns=['epoch','hour','start_s','end_s','channel','reading','observed','fitted','residual'])
    return pd.DataFrame(models),pd.DataFrame(validation),pred


def clock_environment_tables(hourly,cfg):
    long=[]
    for channel in ENV:
        if channel not in hourly:continue
        g=hourly[['epoch','bin',channel,channel+'_samples',channel+'_matched_offset_cycles']].copy()
        g.columns=['epoch','hour','reading','samples','response']
        g['response']=g.response/cfg.nominal_hz*1e6;g['channel']=channel
        g['eligible']=g.samples>=cfg.min_hour_samples
        g['start_s']=g.hour*3600.;g['end_s']=(g.hour+1)*3600.
        long.append(g)
    long=pd.concat(long,ignore_index=True) if long else pd.DataFrame(columns=['epoch','hour','channel','reading','samples','response','eligible','start_s','end_s'])
    joint=pd.DataFrame(columns=['epoch','hour',*ENV,'samples','response','eligible','start_s','end_s'])
    if 'joint_samples' in hourly:
        joint=hourly[['epoch','bin','joint_samples','joint_offset_cycles',*['joint_'+c for c in ENV]]].copy()
        joint.columns=['epoch','hour','samples','response',*ENV]
        joint['response']=joint.response/cfg.nominal_hz*1e6
        joint['eligible']=joint.samples>=cfg.min_hour_samples
        joint['start_s']=joint.hour*3600.;joint['end_s']=(joint.hour+1)*3600.
    for name in ('density_kg_m3', 'density_legacy_kg_m3'):
        if 'joint_'+name in hourly:
            joint[name] = hourly['joint_'+name].to_numpy()
    models,validation,predictions=fit_environment(long,joint,sorted(hourly.epoch.unique()))
    return dict(clock_environment_hourly=long,clock_environment_joint_hourly=joint,
        clock_environment_fits=models,clock_environment_validation=validation,clock_environment_predictions=predictions)
