"""Plan a dashboard-only package update; refuse changes to active capture code."""
import argparse
import ast
import json
from pathlib import Path


def inventory(directory):
    return {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def history_writer(code):
    """Only the read-only history query and its pure density import may differ."""
    tree=ast.parse(code)
    tree.body=[n for n in tree.body if not (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name=='query_history')
               and not (isinstance(n, ast.ImportFrom) and n.module=='pendulum_analysis.air_density')]
    return ast.dump(tree, include_attributes=False)


def plan(source_root, installed_pi, installed_analysis):
    source_root, installed_pi, installed_analysis = map(Path,(source_root,installed_pi,installed_analysis))
    changes={};services=set()
    for package, source, installed in (
        ('pendulum_pi',source_root/'Raspberry.Pi/pendulum_pi',installed_pi),
        ('pendulum_analysis',source_root/'pendulum_analysis',installed_analysis)):
        if not installed.is_dir():
            raise ValueError('An existing full installation is required')
        before,after=inventory(installed),inventory(source)
        changed=sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k))
        changes[package]=changed
        if package=='pendulum_analysis':
            if changed:services.add('pendulum-views')
            if any(k in ('air_density.py','__init__.py') for k in changed):services.add('pendulum-web')
            continue
        for name in changed:
            if name.startswith('static/'):
                continue  # Served from disk on each request; browser refresh suffices.
            if name=='history.py' and name in before and name in after and history_writer(before[name])==history_writer(after[name]):
                services.update(('pendulum-web','pendulum-views'))
            elif name=='web.py':services.add('pendulum-web')
            elif name in ('views.py','environmental.py','phase.py'):services.add('pendulum-views')
            else:raise ValueError(f'{package}/{name} changes a component outside this dashboard-only update; no services stopped')
    return dict(changes=changes,restart_services=sorted(services))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_root',type=Path);parser.add_argument('installed_pi',type=Path);parser.add_argument('installed_analysis',type=Path)
    a=parser.parse_args()
    try:print(json.dumps(plan(a.source_root,a.installed_pi,a.installed_analysis),indent=2))
    except (OSError,ValueError,SyntaxError) as error:raise SystemExit(str(error))
