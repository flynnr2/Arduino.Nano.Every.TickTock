"""Integrate independent swing components from the already calibrated frame."""
from html import escape
import json
import numpy as np
from . import core,distributions,contributions,variation


def frame_events(frame,cfg):
    return dict(sequence=np.nan_to_num(frame.sequence_extended.to_numpy(float),nan=-1).astype(np.int64),
        phase=frame.phase15.to_numpy(np.int64),epoch=frame.epoch.to_numpy(np.int64),
        source_row=frame.source_row.to_numpy(np.int64),valid=frame.calibrated_valid.to_numpy(bool),
        error_us=(frame.full_pps_s.to_numpy(float)-cfg.swing_period_s)*1e6,
        start_s=frame.elapsed_cycles.to_numpy(float)/cfg.nominal_hz,
        end_s=frame.elapsed_cycles.to_numpy(float)/cfg.nominal_hz+frame.full_s.to_numpy(float),
        nominal_s=cfg.swing_period_s)


def render_components(swing,cfg,directory,table_renderer):
    specs=[];checks={}
    for epoch,frame in swing.frame.groupby('epoch',sort=True):
        events=frame_events(frame,cfg)
        phase_note=''
        if cfg.impulse_alignment:
            phase_note=('Impulse-aligned phases: phase 0 contains the impulse-associated half pair.'
                        if frame.impulse_aligned.all() else
                        'Warning: impulse alignment unavailable in this epoch; phases are sequence-based.')
        def annotate(spec):
            if phase_note:
                spec['html']='<p><strong>'+escape(phase_note)+'</strong></p>'+spec['html']
                spec['markdown']=phase_note+'\n\n'+spec['markdown']
            return spec
        try:cycle=core.cycles(events)
        except ValueError:cycle=None
        for title,module in [('Phase-15 distributions',distributions),('Signed phase contributions',contributions)]:
            if cycle is None:
                specs.append(dict(title=f'Epoch {epoch}: {title}',html='<p>No complete consecutive eligible phase-0…14 groups.</p>',markdown='No complete eligible cycles.\n',plots=[]))
                continue
            spec=module.render(cycle,epoch,directory,table_renderer)
            checks[f'e{epoch}_{module.__name__.split(".")[-1]}']=spec.pop('checks')
            spec['title']=f'Epoch {epoch}: '+spec['title'];specs.append(annotate(spec))
        try:spec=variation.render(events,cycle,epoch,directory,cfg,table_renderer)
        except ValueError as error:
            spec=dict(title='Swing variation and stability',html='<p>'+escape(str(error))+'.</p>',markdown=str(error)+'\n',plots=[],checks=dict(status=str(error)))
        checks[f'e{epoch}_variation']=spec.pop('checks')
        spec['title']=f'Epoch {epoch}: '+spec['title'];specs.append(annotate(spec))
        from .motion import render as render_motion
        specs.append(annotate(render_motion(swing,epoch,cfg,directory,table_renderer)))
    (directory/'swing_component_checks.json').write_text(json.dumps(checks,indent=2,allow_nan=False))
    return specs
