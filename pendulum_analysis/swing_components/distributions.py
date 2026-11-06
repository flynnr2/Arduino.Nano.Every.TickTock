"""Phase-15 distributions with honest, dataset-local layout hints."""
import math
from html import escape
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from .rendering import fragment,image,safe_json,CONTROL_STYLE
from ..phase_colors import PHASE_COLORS


def phase_data(x,width):
    mean,median,sd=float(x.mean()),float(np.median(x)),float(x.std())
    delta=x-mean;lo=math.floor(float(x.min())/width)*width
    hi=(math.floor(float(x.max())/width)+1)*width
    counts,_=np.histogram(x,np.arange(lo,hi+width*.5,width))
    assert counts.sum()==len(x)
    qlo,qhi=np.quantile(x,[.001,.999])
    zi=max(0,int(math.floor((qlo-lo)/width)))
    zj=min(len(counts),int(math.ceil((qhi-lo)/width)))
    return dict(n=len(x),mean=mean,median=median,sd=sd,
        skew=float(np.mean(delta**3)/sd**3) if sd else None,
        kurt=float(np.mean(delta**4)/sd**4-3) if sd else None,
        min=float(x.min()),max=float(x.max()),lo=lo,width=width,counts=counts.tolist(),
        zoom=[zi,zj],zoom_hidden=[int(counts[:zi].sum()),int(counts[zj:].sum())])


def layout_hints(a):
    iqrs=np.quantile(a,.75,axis=0)-np.quantile(a,.25,axis=0)
    typical=float(np.median(iqrs))
    raw=max(typical/8.,.001)
    decade=10**math.floor(math.log10(raw))
    width=min([1,2,2.5,5,10],key=lambda m:abs(math.log(raw/(m*decade))))*decade
    med=np.median(a,axis=0)
    seeds=np.flatnonzero(abs(med-np.median(med))>6*typical).tolist() if typical>0 else []
    # Dispersed or numerous offsets do not justify treating one fixed neighbourhood specially.
    if len(seeds)>3 or (len(seeds)>1 and np.sum(np.diff(np.sort(seeds))!=1)>0):seeds=[]
    return width,seeds,typical


def render(cycle,epoch,directory,table_renderer):
    a=cycle['phases'];nominal=cycle['nominal_s'];n=len(a)
    width,seeds,typical=layout_hints(a)
    phases=[phase_data(a[:,p],width) for p in range(15)]
    rows=[dict(phase=p,**{k:v for k,v in d.items() if k not in ['counts','lo','width','zoom','zoom_hidden']}) for p,d in enumerate(phases)]
    stats=pd.DataFrame(rows);csvname=f'swing_e{epoch}_phase15_distributions.csv'
    stats.to_csv(directory/csvname,index=False)
    own=set((p+offset)%15 for p in seeds for offset in range(-2,3))
    peaks=[max(d['counts'])/n*100 for d in phases]
    common=max([v for p,v in enumerate(peaks) if p not in own] or peaks)*1.08
    fig,axes=plt.subplots(5,3,figsize=(15,14.5))
    for p,(d,ax) in enumerate(zip(phases,axes.flat)):
        counts=np.asarray(d['counts'])/n*100;edges=d['lo']+np.arange(len(counts)+1)*width
        ax.stairs(counts,edges,fill=True,color=PHASE_COLORS[p],alpha=.85,lw=0)
        ax.axvline(d['mean'],color='#aa673b',ls='--',lw=1)
        ax.axvline(d['median'],color='#26343d',lw=1)
        ax.set_title(f'Phase {p}'+(' · OWN SCALE' if p in own else ''),loc='left',fontsize=10,fontweight='bold' if p in own else 'normal',color=PHASE_COLORS[p])
        skew='unavailable' if d['skew'] is None else f'{d["skew"]:+.2f}'
        kurt='unavailable' if d['kurt'] is None else f'{d["kurt"]:+.2f}'
        ax.text(.98,.96,f'Skew {skew}\nExcess kurt. {kurt}',transform=ax.transAxes,ha='right',va='top',fontsize=8)
        ax.set(xlim=(edges[0],edges[-1]),ylim=(0,peaks[p]*1.08 if p in own else common),
               xlabel=f'Full period − {nominal:g} s (µs)',ylabel='Cycles / bin (%)')
        ax.grid(axis='y',alpha=.15);ax.tick_params(labelsize=8)
    fig.suptitle(f'Epoch {epoch} · phase-15 full-swing distributions · {n:,} complete cycles',fontsize=14,y=.995)
    fig.tight_layout(rect=(0,0,1,.975))
    filename=f'swing_e{epoch}_phase15_distributions.png';fig.savefig(directory/filename,dpi=140);plt.close(fig)
    caption=(f'Every phase uses the same {n:,} complete cycles and {width:g} µs bins. Solid line: median; dashed line: mean. '
             'All observations are retained. Bold OWN SCALE titles identify independent vertical limits; other phases share one vertical scale.')
    body=f'<p>Epoch {epoch}. Whole-run distributions describe mixtures of operating conditions, drift and changing phase behaviour. Skewness and excess kurtosis are descriptive moments, not tests of normality or impulse identity.</p>'
    body+=image(directory/filename,'Phase-15 full-swing distributions',caption)
    body+=f'<p class="meta">Layout hints: isolated phase medians more than six typical phase IQRs from the median phase level are given separate scales with two neighbours on each side. Here the seed phases are {", ".join(map(str,seeds)) or "none"}. This selects a readable layout; it does not locate the mechanical impulse. Dispersed offsets retain shared scales. Bin width is derived from this dataset alone.</p>'
    body+='''<details><summary>Adjust scales and inspect individual phases</summary><div class="controls">
<label>Vertical units<select id="units"><option value="percent">Cycles per bin (%)</option><option value="count">Cycles per bin (count)</option></select></label>
<label>Horizontal view<select id="extent"><option value="all">Full observed range</option><option value="core">Central 99.8% · exact hidden counts shown</option></select></label>
<label>Own-scale neighbourhood<select id="mask"><option value="2">Offset phases ± 2</option><option value="1">Offset phases ± 1</option><option value="0">Offset phases only</option><option value="none">All shared</option><option value="all">All independent</option></select></label>'''
    body+=f'<label>Bin width<select id="bins"><option value="1">{width:g} µs</option><option value="2">{width*2:g} µs</option><option value="4">{width*4:g} µs</option></select></label></div>'
    body+='<p class="meta">Click a phase title to change its vertical scale. Solid = median; dashed = mean. Zoomed views show exact hidden counts; statistics always use the full population.</p><div id="views"></div><div id="tooltip" role="tooltip"></div></details>'
    view=stats[['phase','mean','median','sd','skew','kurt']].rename(columns={'phase':'Phase','mean':'Mean offset (µs)','median':'Median offset (µs)','sd':'SD (µs)','skew':'Skewness','kurt':'Excess kurtosis'})
    h,md=table_renderer(view,limit=15)
    body+='<details><summary>Phase distribution statistics</summary><div class="scroll">'+h+'</div></details>'
    body+=f'<p><a href="plots/{csvname}">Full phase statistics CSV</a></p>'
    data={f'Epoch {epoch}':dict(metrics={'full':phases},seeds=seeds,nominal=nominal,colors=PHASE_COLORS)}
    body+='<script id="data" type="application/json">'+safe_json(data)+'</script>'
    css=CONTROL_STYLE+'''.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:24px;margin-top:16px}.panel{min-width:0}.phase-title{display:flex;gap:10px;align-items:baseline;width:100%;border:0;background:none;text-align:left;font-size:16px;padding:0;font-weight:400}.own .phase-title{font-weight:700}.scale-label{font-size:11px;text-transform:uppercase}.moments,.tail,.scale-note{font-size:12px;color:#536b78}svg{width:100%;height:228px;display:block;overflow:visible}svg text{fill:#26343d;font:11px system-ui}svg .gridline{stroke:#e9edef;stroke-width:1}svg .axis{stroke:#8c999f;stroke-width:.7}#tooltip{display:none;position:fixed;pointer-events:none;z-index:20;background:white;border:1px solid #a9b6bd;padding:8px;font-size:12px;white-space:pre}'''
    css+='''svg{--ink:#26343d;--mean:#aa673b}'''
    return dict(title='Phase-15 distributions',html=fragment(f'distributions-e{epoch}',body,'distributions.js',css),
        markdown=caption+'\n\n'+md,plots=[filename],checks=dict(bin_width_us=width,layout_seed_phases=seeds,
        own_scale_phases=sorted(own),typical_phase_iqr_us=typical,cycles=n))
