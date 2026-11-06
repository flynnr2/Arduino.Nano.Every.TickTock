"""Signed contributions of all phases to each real complete-cycle error."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Patch
from .rendering import COLORS,CONTROL_STYLE,fragment,image,safe_json,pack_columns


def draw(ax,cycle,values):
    x=cycle['t']/86400.;nominal=cycle['nominal_s'];halfwidth=7*nominal/86400.
    positive=np.zeros(len(x));negative=np.zeros(len(x))
    for phase,color in enumerate(COLORS):
        v=values[:,phase]/1000.;base=np.where(v>=0,positive,negative);top=base+v
        verts=np.stack([np.column_stack([x-halfwidth,base]),np.column_stack([x+halfwidth,base]),
                        np.column_stack([x+halfwidth,top]),np.column_stack([x-halfwidth,top])],axis=1)
        ax.add_collection(PolyCollection(verts,facecolors=color,edgecolors='none',lw=0,rasterized=True))
        positive+=np.maximum(v,0);negative+=np.minimum(v,0)
    total=values.sum(axis=1)/1000.
    gaps=np.flatnonzero(np.diff(cycle['seq'])!=15)+1
    ax.plot(np.insert(x,gaps,x[gaps]),np.insert(total,gaps,np.nan),color='#111111',lw=.7,zorder=4)
    low=min(negative.min(),total.min());high=max(positive.max(),total.max());pad=max(.1,(high-low)*.04)
    ax.set(xlim=(x[0]-halfwidth,x[-1]+halfwidth),ylim=(low-pad,high+pad),
           xlabel='Observed elapsed days',ylabel='Signed cycle deviation (ms)')
    ax.axhline(0,color='#555555',lw=.6);ax.grid(axis='y',alpha=.16);ax.set_axisbelow(True)


def render(cycle,epoch,directory,table_renderer):
    a=cycle['phases'];nominal=cycle['nominal_s'];med=np.median(a,axis=0);delta=a-med
    total=a.sum(axis=1);reference=med.sum();count=len(a)
    direct=((nominal+a/1e6).sum(axis=1)-15*nominal)*1e6
    error=float(np.max(abs(total-direct)));assert error<1e-6
    checks=dict(cycles=count,no_division_by_15=True,nominal_sum_max_error_us=error,
        comparison_sum_max_error_us=float(np.max(abs(delta.sum(axis=1)-(total-reference)))),
        sum_phase_medians_us=float(reference),median_observed_cycle_error_us=float(np.median(total)))
    frame=pd.DataFrame(dict(epoch=epoch,start_seq=cycle['seq'],end_seq=cycle['seq']+14,
        first_csv_line=cycle['source_first'],last_csv_line=cycle['source_last'],elapsed_mid_s=cycle['t'],
        cycle_error_vs_nominal_us=total,cycle_error_vs_phase_medians_us=delta.sum(axis=1)))
    for p in range(15):
        frame[f'phase{p}_nominal_us']=a[:,p];frame[f'phase{p}_departure_us']=delta[:,p]
    export=f'swing_e{epoch}_cycle_phase_contributions.csv.gz';frame.to_csv(directory/export,index=False)
    plots=[];body=f'<p>Epoch {epoch}. Every coloured bar is one real group of 15 consecutive eligible full swings. A phase contribution is its measured duration minus {nominal:g} seconds. The black line is the signed sum: total cycle duration minus {15*nominal:g} seconds. There is no division by 15 and no temporal averaging.</p>'
    for mode,values,title in [('nominal',a,f'Cycle duration − {15*nominal:g} seconds'),
                              ('comparison',delta,'Cycle duration − sum of own-run phase medians')]:
        fig,ax=plt.subplots(figsize=(15,5.8))
        draw(ax,cycle,values);ax.set_title(title,loc='left',fontsize=12)
        fig.legend(handles=[Patch(facecolor=c,label=f'Phase {p}') for p,c in enumerate(COLORS)],
            ncol=8,loc='upper center',bbox_to_anchor=(.5,1),frameon=False,fontsize=9)
        fig.tight_layout(rect=(0,0,1,.86))
        name=f'swing_e{epoch}_contributions_{mode}.png';fig.savefig(directory/name,dpi=140);plt.close(fig)
        plots.append(name)
        body+=image(directory/name,title,'Positive and negative contributions stack separately; black is their signed sum. Axes use this dataset only. At whole-run scale many actual bars overlap within a pixel; the explorer resolves them when zoomed.')
    body+=f'<p>The comparison subtracts the full-run median of each phase separately. Their sum is {reference/1000:+.6f} ms relative to nominal; the median observed cycle error is {np.median(total)/1000:+.6f} ms. They are different statistics. Only the sum of phase references preserves phase-by-phase accounting. These references describe this dataset, not a healthy operating baseline.</p>'
    body+='''<details><summary>Cycle-by-cycle explorer</summary><div class="controls">
<label>Reference<select id="mode"><option value="nominal">Nominal cycle</option><option value="comparison">Own-run phase medians</option></select></label>
<label>Vertical scale<select id="scale"><option value="dataset">This whole dataset</option><option value="visible">Fit visible cycles</option></select></label>
<label style="flex-direction:row;align-items:center"><input id="net" type="checkbox" checked style="width:auto">Show black net line</label>
<label>Start day<input id="day" type="number" min="0" step="0.001" value="0"></label>
<label>Duration (days)<input id="duration" type="number" min="0.00001" step="0.01" value="1"></label>
<button id="apply">Show window</button><button id="all">Whole dataset</button><button id="zoomIn">Zoom in ×2</button><button id="zoomOut">Zoom out ×2</button><button id="previous">Earlier</button><button id="next">Later</button></div><div id="legend"></div><p id="range" class="status" aria-live="polite">Loading cycle data…</p><canvas id="chart" role="img" aria-label="Signed phase contributions to complete-cycle deviation"></canvas><p id="hover">Hover or tap a cycle to inspect its contributions.</p><div id="hover-values"></div></details>'''
    body+=f'<p><a href="plots/{export}">Every retained cycle and its signed phase contributions</a></p>'
    def limits(v):
        lo=float(np.minimum(v,0).sum(axis=1).min());hi=float(np.maximum(v,0).sum(axis=1).max());pad=max(100.,(hi-lo)*.04)
        return [lo-pad,hi+pad]
    packed=pack_columns([('t',cycle['t'],'<f8','Float64Array'),('seq',cycle['seq'],'<f8','Float64Array'),
                         ('values',a,'<f4','Float32Array')])
    payload=dict(packed=packed,medians=med.tolist(),colors=COLORS,nominal_s=nominal,
                 limits=dict(nominal=limits(a),comparison=limits(delta)))
    body+='<script id="data" type="application/json">'+safe_json(payload)+'</script>'
    css=CONTROL_STYLE+'''#legend,#hover-values{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:13px;margin:14px 0}#legend span,#hover-values span{white-space:nowrap}#legend i{display:inline-block;width:15px;height:12px;margin-right:5px}#hover-values span{min-width:150px;font-variant-numeric:tabular-nums}'''
    checks['interactive_max_display_rounding_us']=packed['max_display_error_us']
    return dict(title='Signed phase contributions',html=fragment(f'contributions-e{epoch}',body,'contributions.js',css),
        markdown=f'All {count:,} complete cycles; exact signed sums verified. No division by 15.\n',plots=plots,checks=checks)
