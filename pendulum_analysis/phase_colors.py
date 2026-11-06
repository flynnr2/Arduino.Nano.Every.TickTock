"""One sequence-phase colour contract for static and interactive reports.

Preserve the tab20 ordering originally used by Pendulum behaviour over time.
Both half phases of a full swing inherit its colour; shape/style identifies side.
"""
import numpy as np

PHASE_COLORS = (
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
    '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
    '#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5',
)


def phase_colors(phases, *, half=False):
    phases = np.asarray(phases)
    valid = np.isfinite(phases) & (phases >= 0) & (phases < (30 if half else 15)) & (phases == np.floor(phases))
    indices = np.where(valid, phases, 0).astype(int)
    if half:
        indices //= 2
    return np.where(valid, np.asarray(PHASE_COLORS)[indices], '#777777')


def phase_colormap():
    from matplotlib.colors import ListedColormap
    return ListedColormap(PHASE_COLORS)


def phase_ticks(ax, *, half=False, axis='x'):
    """Identify categorical axes without replacing a heatmap's value scale."""
    ticks = ax.get_xticks() if axis == 'x' else ax.get_yticks()
    labels = ax.get_xticklabels() if axis == 'x' else ax.get_yticklabels()
    for tick, label, color in zip(ticks, labels, phase_colors(ticks, half=half)):
        if 0 <= tick < (30 if half else 15) and tick == int(tick):
            label.set_color(color)
            label.set_fontweight('bold')


def phase_legend(fig):
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(facecolor=c, label=str(p)) for p, c in enumerate(PHASE_COLORS)],
               ncol=15, loc='upper center', frameon=False, fontsize=8,
               title='Full-swing phase · tick ○ / tock △ share the same colour')


def phase_legend_html():
    chips = ''.join(f'<span style="white-space:nowrap"><i style="display:inline-block;width:14px;height:12px;background:{c};margin-right:4px"></i>{p}</span>'
                    for p, c in enumerate(PHASE_COLORS))
    return '<p class="meta">Full-swing phase colours are shared throughout this report. Half phases 2p (tick ○) and 2p+1 (tock △) inherit phase p. Complete-cycle averages combine all 15 colours; heatmap shading represents the labelled numerical value.</p><div aria-label="Full-swing phase colour key" style="display:flex;flex-wrap:wrap;gap:8px 16px">'+chips+'</div>'
