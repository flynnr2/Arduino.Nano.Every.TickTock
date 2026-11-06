"""Phase identity must survive changes of section, side, origin and display size."""
import base64
import gzip
import matplotlib.pyplot as plt
from matplotlib.colors import to_hex, to_rgba_array
import numpy as np

from pendulum_analysis.phase_colors import PHASE_COLORS, phase_colors


def test_palette_preserves_the_original_behaviour_chart_key():
    original=plt.get_cmap('tab20').colors
    order=list(range(0,20,2))+list(range(1,10,2))
    assert PHASE_COLORS==tuple(to_hex(original[i]) for i in order)
    # A missing phase must never reassign colours to the remaining observations.
    assert phase_colors([0,8,14]).tolist()==[PHASE_COLORS[i] for i in (0,8,14)]
    assert phase_colors([16,17,28,29],half=True).tolist()==[PHASE_COLORS[i] for i in (8,8,14,14)]
    assert phase_colors([-1,np.nan,15]).tolist()==['#777777']*3


def test_motion_side_markers_share_parent_phase_and_payload_keeps_explicit_origin():
    from test_swing_motion import frame, analyze
    from pendulum_analysis.swing_components.motion import draw_series, packed
    f=frame(45)
    f.phase15=(f.sequence_extended-4)%15
    tables,_=analyze(f)
    obs=tables['swing_motion_observations']
    cycles=tables['swing_motion_cycles']
    fig,axes=plt.subplots(2,1)
    draw_series(axes,obs,cycles)
    for ax in axes:
        for side in ax.collections:
            np.testing.assert_allclose(side.get_facecolors()[:,:3],to_rgba_array(phase_colors(obs.phase15))[:,:3])
        assert ax.lines[0].get_linestyle()=='-'
        assert ax.lines[1].get_linestyle()=='--'
    plt.close(fig)
    payload=packed(obs)
    raw=gzip.decompress(base64.b64decode(payload['gzip_base64']))
    column=payload['columns']['phase']
    phases=np.frombuffer(raw,dtype='<f8',offset=column['offset'],count=column['length'])
    np.testing.assert_array_equal(phases,f.phase15)
