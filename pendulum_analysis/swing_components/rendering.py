"""Self-contained component fragments, isolated from sibling controls."""
from pathlib import Path
from html import escape
import base64
import gzip
import json
import re
import numpy as np
from ..report_assets import png_data_uri
from ..phase_colors import PHASE_COLORS

ASSETS=Path(__file__).parent/'assets'
COLORS=PHASE_COLORS


def safe_json(data):
    return json.dumps(data,separators=(',',':'),allow_nan=False).replace('<','\\u003c')


def fragment(namespace,body,script=None,css=''):
    ids=re.findall(r'\bid="([^"]+)"',body)
    for key in ids:
        body=body.replace(f'id="{key}"',f'id="{namespace}--{key}"')
        css=re.sub(r'#'+re.escape(key)+r'(?![\w-])','#'+namespace+'--'+key,css)
    # Prefix every selector so multiple epochs/components can coexist safely.
    css=re.sub(r'(^|})([^@{}][^{}]*)\{',lambda m:m[1]+','.join('#'+namespace+' '+s.strip() for s in m[2].split(','))+'{',css)
    result=f'<div id="{namespace}" class="swing-component"><style>{css}</style>{body}</div>'
    if script:
        code=(ASSETS/script).read_text().replace('document.getElementById(','get(')
        result+=f'<script>(()=>{{const get=id=>document.getElementById({safe_json(namespace+"--")}+id);return '+code+'})();</script>'
    return result


def image(path,title,caption):
    return f'<figure><img loading="lazy" src="{png_data_uri(path)}" alt="{escape(title)}"><figcaption>{escape(caption)}</figcaption></figure>'


def pack_columns(columns):
    parts=[];offset=0;schema={};error=0.
    for name,values,dtype,js in columns:
        a=np.asarray(values,dtype=dtype)
        pad=(-offset)%8
        if pad:parts.append(bytes(pad));offset+=pad
        schema[name]=dict(offset=offset,length=a.size,type=js)
        raw=a.tobytes();parts.append(raw);offset+=len(raw)
        if dtype=='<f4':
            finite=np.isfinite(values)
            if finite.any():error=max(error,float(np.max(abs(a[finite].astype(float)-np.asarray(values)[finite]))))
    return dict(columns=schema,gzip_base64=base64.b64encode(gzip.compress(b''.join(parts),compresslevel=4,mtime=0)).decode(),
                max_display_error_us=error)


def pack_series(series):
    result=pack_columns([('t',series.t,'<f8','Float64Array'),('mean',series.mean,'<f4','Float32Array'),
        ('range',series.range,'<f4','Float32Array'),('low',series.low,'<f4','Float32Array'),
        ('seq',series.seq,'<f8','Float64Array'),('run',series.run,'<u4','Uint32Array')])
    result['points']=len(series.t)
    return result


CONTROL_STYLE='''.controls{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:end;margin:16px 0}label{display:flex;flex-direction:column;font-size:13px;max-width:100%}button,select,input{font:inherit;color:inherit;padding:7px;max-width:100%;box-sizing:border-box}button{cursor:pointer}input{width:115px}canvas{display:block;width:100%;height:540px;border:1px solid #dce4e8}.meta,.status{font-size:13px;color:#536b78}.status{min-height:20px}.table{overflow:auto}.stats{font-variant-numeric:tabular-nums}'''
