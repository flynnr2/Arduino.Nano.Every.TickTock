"""Compact environmental evidence, with shared methods and clock support."""
from html import escape
import pandas as pd
import matplotlib.pyplot as plt
from ..report_assets import png_data_uri
from .common import ENV
from .environment_models import JOINT

LABELS={'temperature_C':'Temperature (°C)','humidity_pct':'Humidity (% RH)','pressure_hPa':'Pressure (hPa)',JOINT:'All three together', 'density':'CIPM-2007 density', 'temperature_density':'Temperature + CIPM-2007 density', 'legacy_density':'Previous density', 'temperature_legacy_density':'Temperature + previous density', 'density_matched_sensors':'Three sensors on density-matched hours'}
BLUE='#307c94'
ORANGE='#bd6a34'


def draw(tables,prefix,directory,unit):
    """Three associations and one combined residual; no repeated residual panels."""
    models=tables[prefix+'_fits'];pred=tables[prefix+'_predictions'];hourly=tables[prefix+'_hourly']
    fig=plt.figure(figsize=(14,9),layout='constrained')
    grid=fig.add_gridspec(3,3,height_ratios=(1.2,1,.8))
    response_label='Period − nominal (µs)' if unit=='µs' else 'Clock offset (ppm)'
    associations=[]
    for column,channel in enumerate(ENV):
        ax=fig.add_subplot(grid[0,column],sharey=associations[0] if associations else None)
        associations.append(ax)
        data=hourly.loc[hourly.channel.eq(channel)&hourly.eligible]
        ax.scatter(data.reading,data.response,s=8,alpha=.4,color=BLUE,edgecolors='none')
        fitted=pred.loc[pred.channel.eq(channel)]
        for _,group in fitted.groupby('epoch'):
            g=group.sort_values('reading');ax.plot(g.reading,g.fitted,color=ORANGE,lw=1)
        if not len(fitted):
            selected=models.loc[models.channel.eq(channel)]
            status='; '.join(sorted(set(selected.status))) or 'No eligible paired hours'
            ax.text(.03,.96,status,ha='left',va='top',wrap=True,fontsize=8,transform=ax.transAxes)
        ax.set_xlabel(LABELS[channel])
        if column==0:ax.set_ylabel(response_label)
        else:ax.tick_params(labelleft=False)
    observed=fig.add_subplot(grid[1,:])
    residual=fig.add_subplot(grid[2,:],sharex=observed)
    fitted=pred.loc[pred.channel.eq(JOINT)]
    for _,group in fitted.groupby('epoch'):
        g=group.sort_values('hour')
        for _,run in g.groupby(g.hour.diff().ne(1).cumsum()):
            t=(run.start_s+run.end_s)/2/86400.
            observed.plot(t,run.observed,color=BLUE,lw=.75,label='Observed')
            observed.plot(t,run.fitted,color=ORANGE,lw=1,label='All-three fit')
            residual.plot(t,run.residual,color=BLUE,lw=.75)
    if len(fitted):
        handles,labels=observed.get_legend_handles_labels();unique=dict(zip(labels,handles))
        observed.legend(unique.values(),unique.keys(),fontsize=9,frameon=False,ncol=2,loc='upper left')
    else:
        for ax in (observed,residual):
            ax.text(.5,.5,'Insufficient shared all-three coverage',ha='center',transform=ax.transAxes)
    observed.set(ylabel=response_label,title='Combined fit · shared all-three coverage only')
    observed.tick_params(labelbottom=False)
    residual.set(xlabel='Observed elapsed days',ylabel=f'Remaining ({unit})')
    residual.axhline(0,color='#777777',lw=.6)
    for ax in fig.axes:
        ax.grid(axis='y',alpha=.13)
        for side in ('top','right'):ax.spines[side].set_visible(False)
    filename=prefix+'_associations.png';fig.savefig(directory/filename,dpi=130);plt.close(fig)
    return filename


def render(tables,prefix,directory,unit,title,table_renderer):
    filename=draw(tables,prefix,directory,unit)
    models=tables[prefix+'_fits'].copy();models['Sensor']=models.channel.map(LABELS)
    models['Coverage (days)']=models.apply(lambda r:f'{r.start_day:.3g}–{r.end_day:.3g}' if pd.notna(r.start_day) else 'unavailable',axis=1)
    validation=tables[prefix+'_validation'].copy()
    summary=models.merge(validation[['epoch','channel','model_rmse','constant_rmse','status']],on=['epoch','channel'],suffixes=('','_validation'),validate='one_to_one')
    def check(row):
        if pd.isna(row.model_rmse) or pd.isna(row.constant_rmse):return row.status_validation
        return f'{row.model_rmse:.3g} / {row.constant_rmse:.3g}'
    summary['Later fit / constant']=summary.apply(check,axis=1)
    cols={'epoch':'Epoch','Sensor':'Sensor','hours':'Hours','Coverage (days)':'Days covered',
          'residual_rmse':f'Full-run residual RMS ({unit})','Later fit / constant':f'Later fit / constant RMS ({unit})','status':'Status'}
    h,md=table_renderer(summary[list(cols)].rename(columns=cols),limit=len(summary),digits=4)
    body=f'<figure><img loading="lazy" src="{png_data_uri(directory/filename)}" alt="{escape(title)}"><figcaption>Each point is a paired hourly mean. Blue: observed response; orange: descriptive fit. The three sensor panels share a response scale. The combined-fit panels share a time axis and use only the all-three population; gaps and epochs stay separate.</figcaption></figure>'
    body+='<p class="meta">Density models use CIPM-2007 (CO₂ assumed 400 ppm), density alone or temperature plus density. Previous Buck/ideal-mixture density is retained for comparison. All five density-comparison models use the same complete, in-range hours; the three-sensor control on those hours is labelled separately. Density is calculated before averaging; its recommended range is 15–27 °C and 600–1100 hPa.</p>'
    body+='<div class="scroll">'+h+'</div><p class="meta">Later fit / constant: prediction error on the final 30% of eligible hours, using the first 70% to fit either the environmental model or a constant mean. Lower RMS is better. Compare the two numbers within each row; rows with different coverage do not measure the same period.</p>'
    density=models.loc[models.channel.isin(['density','temperature_density','legacy_density','temperature_legacy_density','density_matched_sensors'])]
    if len(density):
        columns={'epoch':'Epoch','Sensor':'Model','hours':'Matched hours','temperature_C_slope':f'Temperature ({unit}/°C)',
                 'density_kg_m3_slope':f'CIPM density ({unit}/(kg/m³))','density_legacy_kg_m3_slope':f'Previous density ({unit}/(kg/m³))','status':'Status'}
        density_html,_=table_renderer(density[list(columns)].rename(columns=columns),limit=len(density),digits=4)
        body+='<h3>Density-model coefficients</h3><div class="scroll">'+density_html+'</div>'
    joint=models.loc[models.channel.eq(JOINT)]
    adjusted={'epoch':'Epoch','hours':'Shared hours','temperature_C_slope':f'Temperature ({unit}/°C)',
        'humidity_pct_slope':f'Humidity ({unit}/% RH)','pressure_hPa_slope':f'Pressure ({unit}/hPa)',
        'predictor_rank':'Predictor rank','condition_number':'Scaled condition number','status':'Status'}
    h,md_joint=table_renderer(joint[list(adjusted)].rename(columns=adjusted),limit=len(joint),digits=4)
    slopes={'epoch':'Epoch','Sensor':'Sensor','samples':'Paired observations','slope_per_unit':f'Single-sensor slope ({unit}/unit)','r_squared':'R²','status':'Status'}
    hs,md_slopes=table_renderer(models[list(slopes)].rename(columns=slopes),limit=len(models),digits=4)
    validation['Sensor']=validation.channel.map(LABELS)
    checks={'epoch':'Epoch','Sensor':'Sensor','train_hours':'Train hours','test_hours':'Test hours',
          'model_rmse':f'Fit RMSE ({unit})','constant_rmse':f'Constant RMSE ({unit})','status':'Status'}
    hv,md_validation=table_renderer(validation[list(checks)].rename(columns=checks),limit=len(validation),digits=4)
    body+='<details><summary>Slopes, separability and validation detail</summary><h4>Combined fit and separability</h4><div class="scroll">'+h+'</div><h4>Single-sensor fits</h4><div class="scroll">'+hs+'</div><h4>Prediction of later hours</h4><div class="scroll">'+hv+'</div></details>'
    markdown=f'![{title}](plots/{filename})\n\n'+md+'\n\n### Combined fit and separability\n\n'+md_joint+'\n\n'+md_slopes+'\n\n### Prediction of later hours\n\n'+md_validation
    return dict(title=title,html=body,markdown=markdown,plots=[filename])


def render_combined(tables,has_swing,directory,table_renderer):
    """One environmental narrative; the capture clock is supporting evidence."""
    explanation=('Temperature, humidity and pressure are fitted separately using their own valid paired hours. '
        'The combined fit uses only timing observations valid for all three sensors together. '
        'These are descriptive associations: shared drift can produce a relationship. No environmental correction is applied to the reported timing.')
    methods=('Single-sensor means use the same timing and sensor observations. The combined model uses the exact same underlying observations for all three sensors. '
        'Missing or stale measurements are excluded; readings are never filled from another sensor. Epochs are fitted separately. '
        'Full-run fits require 24 eligible hours. Later-hour checks require at least 24 training and 12 test hours; the constant baseline uses exactly the same split and population. '
        'Adjusted combined slopes hold the other two measured variables fixed. Slopes are withheld for rank-deficient centred/scaled predictors or a scaled condition number above 30, matching the dashboard policy; a combined prediction may still be available. '
        'Hourly values can remain serially dependent; no independent-sample significance claim is made.')
    clock=render(tables,'clock_environment',directory,'ppm','Capture-clock response to environment',table_renderer)
    body='<p>'+explanation+'</p>';markdown=explanation;plots=[]
    if has_swing:
        lookup=dict(tables)
        lookup['swing_environment_fits']=tables['swing_environment_all_fits']
        lookup['swing_environment_validation']=tables['swing_environment_all_validation']
        lookup['swing_environment_hourly']=tables['swing_environment_hourly'].rename(columns={'period_error_us':'response','cycles':'samples'})
        swing=render(lookup,'swing_environment',directory,'µs','Swing period and environment',table_renderer)
        response=('The pendulum response is the PPS-calibrated mean period minus nominal, in microseconds. '
            'Each observation is a complete 15-swing group with every sensor reading valid for the channel being fitted; hours require at least 30 nominal minutes of complete groups.')
        body+='<h3>Swing period and environment</h3><p>'+response+'</p>'+swing['html']
        markdown+='\n\n### Swing period and environment\n\n'+response+'\n\n'+swing['markdown']
        body+='<details class="environment-clock"><summary>Capture-clock environment · calibration support</summary><p>The clock response is the capture oscillator’s frequency offset against PPS, in ppm. It is a separate response with its own matched population, not a second estimate of pendulum sensitivity. Each hour requires 1,800 matched one-second intervals.</p>'+clock['html']+'</details>'
        plots+=swing['plots']
    else:body+='<p>Capture-oscillator frequency offset against PPS, in ppm. Each hour requires 1,800 matched one-second intervals.</p>'+clock['html']
    markdown+='\n\n### Capture-clock environment · calibration support\n\n'+clock['markdown']
    body+='<details><summary>Environmental fit methods</summary><p>'+methods+'</p></details>'
    markdown+='\n\n### Environmental fit methods\n\n'+methods
    plots+=clock['plots']
    return dict(title='Environment and remaining variation',html=body,markdown=markdown,plots=plots)
