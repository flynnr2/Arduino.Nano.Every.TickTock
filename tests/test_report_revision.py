import json
import re
import numpy as np
from pendulum_analysis.suite.__main__ import run
from pendulum_analysis.suite.common import Settings
from test_suite import clock_frame,swings_frame


def test_report_components_optional_defaults_and_collapsing(tmp_path):
    clock_frame(400).drop(columns='source_row').to_csv(tmp_path/'PCPS.CSV',index=False)
    swings_frame(np.arange(150)).drop(columns='source_row').to_csv(tmp_path/'PCSW.CSV',index=False)
    out=tmp_path/'report';result=run(tmp_path,out,Settings(),progress=lambda _:None)
    text=(out/'report.html').read_text()
    for title in ['Phase-15 distributions','Signed phase contributions','Swing variation and stability','Median local SD','Velocity and amplitude',
                  'Environment and remaining variation','Swing period and environment','Combined fit and separability']:
        assert title in text
    for title in ['Clock and environmental validity','Accumulated timing error','pendulum frequency and four-hour variation']:
        assert title not in text
    assert all(not ('_raw_' in f or '_phase_polars' in f or '_mean_median' in f or '_jitter' in f or re.match('swing_e.*_frequency.png',f)) for f in result['plots'])
    assert not any('_pca_' in f or f.endswith('_spectra.png') for f in result['plots'])
    ids=re.findall(r'\bid=[\'"]([^\'"]+)[\'"]',text)
    # Dynamically generated IDs live inside JS; source attributes must all be unique.
    assert len(ids)==len(set(ids))
    assert 'report-toggle-all' in text and 'sections().forEach' in text
    assert text.count('>Environment and remaining variation</summary>')==1
    positions=[text.index(f'>{title}</summary>') for title in ('Epoch 0: Phase-15 distributions','Epoch 0: Signed phase contributions','Epoch 0: Swing variation and stability','Epoch 0: Velocity and amplitude','Environment and remaining variation','Phase-bin exposure')]
    assert positions==sorted(positions)
    assert '<details class="report-section" id="phase-exposure"><summary>' in text
    assert 'Mean (ms)' in text and 'Std dev (µs)' in text
    assert '<details class="environment-clock"><summary>Capture-clock environment' in text
    assert 'Clock environment and remaining variation' not in text
    assert text.count('>Environmental fit methods</summary>')==1
    assert 'Later fit / constant RMS (µs)' in text
    assert 'Those two half-bin counts are equal' in text
    assert 'eligible partial cycles' in text
    assert text.count('data:image/png;base64,')==len(result['plots'])
    for block in (out/'report.md').read_text().split('\n\n'):
        rows=[r for r in block.splitlines() if r.startswith('|')]
        if rows:
            assert all(r.endswith('|') for r in rows)
            assert len({tuple(i for i,c in enumerate(r) if c=='|') for r in rows})==1
