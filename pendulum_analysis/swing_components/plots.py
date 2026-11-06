"""Static figures for independent single-dataset reports."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from ..phase_colors import phase_colors, phase_ticks

plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                     'agg.path.chunksize':10000,'path.simplify_threshold':.1})
BLUE='#307c94'; ORANGE='#bd6a34'; GREEN='#408471'


def save(fig, path):
    fig.tight_layout(rect=(0,0,1,.9) if fig.legends else (0,0,1,1))
    fig.savefig(path,dpi=145)
    plt.close(fig)


def broken(t,y,run):
    gaps=np.flatnonzero(np.diff(run)!=0)+1
    return np.insert(t/86400.,gaps,t[gaps]/86400.),np.insert(y,gaps,np.nan)


def rolling(series,path,label):
    fig,axes=plt.subplots(2,1,figsize=(14,7.5),sharex=True)
    xx,yy=broken(series.t,series.mean,series.run)
    axes[0].plot(xx,yy,color=BLUE,lw=.55)
    axes[0].axhline(0,color='#777777',lw=.6)
    axes[0].set(title=f'{label} · 15-swing arithmetic average · updated every swing',
                ylabel=f'Mean full period − {series.nominal_s:g} s (µs)')
    xx,yy=broken(series.t,series.range,series.run)
    axes[1].plot(xx,yy,color=ORANGE,lw=.5)
    axes[1].set(title='Trailing two-minute range of those averaged periods',
        ylabel='Local peak-to-peak variation (µs)',xlabel='Elapsed days from first source row',ylim=(0,None))
    for ax in axes:
        ax.grid(axis='y',alpha=.18)
        ax.set_xlim(series.t[0]/86400.,series.t[-1]/86400.)
    save(fig,path)


def stability(table,path,label):
    fig,ax=plt.subplots(figsize=(11,5.5))
    if len(table):
        for col,color,name in [('adev_ppm',BLUE,'Allan'),('hdev_ppm',ORANGE,'Hadamard')]:
            positive=table[col]>0
            ax.loglog(table.loc[positive,'tau_s'],table.loc[positive,col],color=color,lw=1,label=name)
            strong=positive&~table.low_support
            weak=positive&table.low_support
            ax.scatter(table.loc[strong,'tau_s'],table.loc[strong,col],s=20,color=color,zorder=3)
            ax.scatter(table.loc[weak,'tau_s'],table.loc[weak,col],s=24,facecolors='none',edgecolors=color,zorder=3)
        ax.legend(frameon=False)
        if not table[['adev_ppm','hdev_ppm']].gt(0).any().any():
            ax.text(.5,.5,'Zero measured variation at the supported scales',ha='center',transform=ax.transAxes)
    ax.set(title=f'{label} · frequency stability of complete nonoverlapping cycles',
           xlabel='Averaging time τ (s)',ylabel='Fractional frequency deviation (ppm)')
    ax.grid(which='both',alpha=.17)
    save(fig,path)


def pca(model,cycle,pattern_path,score_path,label):
    fraction=model['fraction'];loads=model['loadings']
    fig,axes=plt.subplots(1,2,figsize=(14,4.7))
    axes[0].bar(np.arange(1,len(fraction)+1),fraction*100,color=BLUE)
    axes[0].set(title=f'{label} · covariance PCA',xlabel='Component',ylabel='Explained phase variance (%)',
                xticks=np.arange(1,len(fraction)+1))
    for k,color in enumerate([BLUE,ORANGE,GREEN][:min(3,len(loads))]):
        axes[1].plot(np.arange(15),loads[k],linestyle=['-','--',':'][k],color='#555555',lw=1,
                     label=f'PC{k+1} · {fraction[k]*100:.2f}%')
        axes[1].scatter(np.arange(15),loads[k],c=phase_colors(np.arange(15)),marker=['o','^','s'][k],s=18,zorder=3)
    axes[1].axhline(0,color='#777777',lw=.5)
    axes[1].set(title='Phase loadings · each component fitted to this dataset',
                xlabel='Analysis phase',ylabel='Loading',xticks=np.arange(15))
    axes[1].legend(frameon=False)
    phase_ticks(axes[1])
    axes[1].grid(axis='y',alpha=.18)
    save(fig,pattern_path)
    n=model['scores'].shape[1]
    fig,axes=plt.subplots(n,1,figsize=(14,2.5*n),sharex=True,squeeze=False)
    for k,ax in enumerate(axes[:,0]):
        for sl in cycle['runs']:
            ax.plot(cycle['t'][sl]/86400.,model['scores'][sl,k],color=[BLUE,ORANGE,GREEN][k],lw=.5)
        ax.set(ylabel=f'PC{k+1} score (µs)',title=f'{fraction[k]*100:.2f}% of phase-duration variance')
        ax.grid(axis='y',alpha=.18)
    axes[-1,0].set_xlabel('Elapsed days from first source row')
    save(fig,score_path)


def spectra(results,path,label):
    n=len(results)
    fig,axes=plt.subplots(n,1,figsize=(13,4*n),squeeze=False)
    for result,ax in zip(results,axes[:,0]):
        f=result['frequency_hz'];p=result['psd_us2_per_hz'];good=(f>0)&(p>0)
        ax.loglog(f[good],p[good],color=BLUE,lw=.9)
        ax.set(title=f'{label} · {result["segment_seconds"]/3600:.2f} h segments · {result["segments"]} segments · {100*result["coverage_fraction"]:.1f}% of eligible cycles',
               xlabel='Frequency (Hz)',ylabel='Mean-period PSD (µs²/Hz)')
        ax.grid(which='both',alpha=.15)
    save(fig,path)
