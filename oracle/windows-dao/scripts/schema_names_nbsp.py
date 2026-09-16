#!/usr/bin/env python3
"""Verify native leading/trailing NBSP name controls."""
import argparse, hashlib, json
from pathlib import Path
B = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument('matrix', type=Path)
ap.add_argument('outbox', type=Path)
ap.add_argument('source_revision')
ap.add_argument('report', type=Path)
args = ap.parse_args()
O = args.outbox
M = json.loads(args.matrix.read_text())

def req(x, m):
    if not x:
        raise AssertionError(m)

def ident(p):
    d = p.read_bytes()
    return {'size': len(d), 'sha256': hashlib.sha256(d).hexdigest()}
ids = [p['id'] for p in M['probes']]
expected = {*(f'r{r}-probe-{i}.mdb' for r in (1, 2) for i in ids), 'r1-result.json', 'r2-result.json', 'workers.json', 'log.txt', 'exit.txt'}
req({p.name for p in O.iterdir() if p.is_file()} == expected, 'inventory')
req((O / 'exit.txt').read_text().strip() == '0', 'exit')
rows = []
env = None
for r in (1, 2):
    d = json.loads((O / f'r{r}-result.json').read_text())
    req(d['status'] == 'pass' and d['error'] is None, 'worker')
    env = d['environment'] if env is None else env
    req(d['environment'] == env, 'provider')
    req([x['id'] for x in d['probes']] == ids, 'order')
    for spec, e in zip(M['probes'], d['probes']):
        req(len(e['attempts']) == 1 and e['attempts'][0]['success'] and (e['attempts'][0]['error'] is None), 'success')
        name = spec['names'][0]
        req(e['attempts'][0]['requested']['value'] == name, 'requested exact')
        cap = e['capture']
        p = O / e['file']
        req(cap['before'] == cap['after'] == ident(p), 'read identity')
        tables = cap['snapshot']['tables']
        if spec['role'] == 'table':
            saved = [t['name']['value'] for t in tables]
        elif spec['role'] == 'column':
            saved = [f['name']['value'] for t in tables if t['name']['value'] == 'Probe' for f in t['fields'][2:]]
        else:
            saved = [i['name']['value'] for t in tables if t['name']['value'] == 'Probe' for i in t['indexes']]
        req(saved[-1] == name, 'saved exact')
        rows.append({'replica': r, 'id': spec['id'], 'role': spec['role'], 'name': name, 'image': ident(p)})
report = {'document_type': 'dao_cp1252_nbsp_name_boundary', 'status': 'accepted', 'source_revision': args.source_revision, 'environment': env, 'matrix': ident(args.matrix), 'producer': ident(B / 'schema_names_discovery.ps1'), 'evaluator': ident(Path(__file__)), 'coverage': {'replicas': 2, 'roles': 3, 'positions': 2, 'mdbs': 12}, 'observations': rows}
args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
