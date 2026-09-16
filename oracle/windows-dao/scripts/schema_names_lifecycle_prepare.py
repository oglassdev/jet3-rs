#!/usr/bin/env python3
"""Prepare exact CLI mutation stages for paired DAO name comparisons."""
import argparse, copy, hashlib, json, shutil, subprocess, zipfile
from pathlib import Path

def ident(p):
    b = p.read_bytes()
    return {'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()}

def cells(fields, values):
    result = []
    for f, v in zip(fields, values):
        if v is None:
            result.append(None)
        elif f['type'] == 4:
            result.append({'long': v})
        else:
            result.append({{10: 'text', 12: 'memo'}[f['type']]: list(v.encode('cp1252'))})
    return result

def recipes(case):
    if case['id'] == 'ordering':
        name = case['tables'][0]['name']
        return [{'operation': 'insert', 'table': name, 'id': 3, 'values': [3, 'new z', 'new a', 'new á', 'new æ', 'new b', 'é', 'note']}, {'operation': 'update', 'table': name, 'id': 3, 'column': 0, 'value': 4}, {'operation': 'replace', 'table': name, 'id': 4, 'values': [4, 'next z', 'next a', 'next á', 'next æ', 'next b', '', '']}, {'operation': 'delete', 'table': name, 'id': 4}]
    return [{'operation': 'insert', 'table': 'Pärent', 'id': 2, 'values': [2, '']}, {'operation': 'insert', 'table': 'Chîld', 'id': 20, 'values': [20, 2, '']}, {'operation': 'update', 'table': 'Chîld', 'id': 20, 'column': 1, 'value': 99, 'refused': True}, {'operation': 'delete', 'table': 'Pärent', 'id': 2, 'refused': True}, {'operation': 'update', 'table': 'Chîld', 'id': 20, 'column': 1, 'value': 1}, {'operation': 'replace', 'table': 'Chîld', 'id': 20, 'values': [20, 1, 'updated €']}, {'operation': 'delete', 'table': 'Chîld', 'id': 20}, {'operation': 'delete', 'table': 'Pärent', 'id': 2}]

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--candidates', type=Path, required=True)
    p.add_argument('--native', type=Path, required=True)
    p.add_argument('--native-matrix', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    matrix = json.loads((a.candidates / 'matrix.json').read_text())
    discovery = json.loads(a.native_matrix.read_text())
    groups = []
    for origin, plan in [('candidate', matrix), ('native', discovery)]:
        for case in plan['accepted']:
            if case['id'] not in ('ordering', 'relation'):
                continue
            for replica in (1, 2):
                name = f"{origin}-{case['id']}-r{replica}"
                d = a.output / name
                d.mkdir()
                source = a.candidates / f"r{replica}-candidate-{case['id']}.mdb" if origin == 'candidate' else a.native / f"r{replica}-accepted-{case['id']}.mdb"
                original = ident(source)
                shutil.copyfile(source, d / 'initial.mdb')
                current = d / 'current.mdb'
                shutil.copyfile(source, current)
                locations = {}
                events = []
                state = {t['name']: copy.deepcopy(t['rows']) for t in case['tables']}
                for ordinal, op in enumerate(recipes(case), 1):
                    table = next((t for t in case['tables'] if t['name'] == op['table']))
                    fields = table['fields']
                    key = (op['table'], op['id'])
                    req = {'operation': op['operation'], 'table': op['table']}
                    if op['operation'] != 'insert':
                        req['row'] = locations[key]
                    if op['operation'] in ('insert', 'replace'):
                        req['values'] = cells(fields, op['values'])
                    if op['operation'] == 'update':
                        req.update({'column': op['column'], 'value': cells([fields[op['column']]], [op['value']])[0]})
                    stem = f"{ordinal:02d}-{op['operation']}"
                    (d / (stem + '.request.json')).write_text(json.dumps(req, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
                    before = ident(current)
                    r = subprocess.run([str(a.binary.resolve()), 'mutate', str(current), '--input', str(d / (stem + '.request.json'))], capture_output=True, text=True)
                    (d / (stem + '.stdout')).write_text(r.stdout)
                    (d / (stem + '.stderr')).write_text(r.stderr)
                    refused = op.get('refused', False)
                    assert (r.returncode != 0) == refused, (name, op, r.stderr)
                    if refused:
                        assert ident(current) == before
                    else:
                        receipt = json.loads(r.stdout)
                        locations[key] = receipt['row']
                        rows = state[op['table']]
                        idfield = fields[0]['name']
                        if op['operation'] == 'insert':
                            rows.append(dict(zip([f['name'] for f in fields], op['values'])))
                        elif op['operation'] == 'delete':
                            rows[:] = [v for v in rows if v[idfield] != op['id']]
                        else:
                            row = next((v for v in rows if v[idfield] == op['id']))
                            if op['operation'] == 'replace':
                                row.update(dict(zip([f['name'] for f in fields], op['values'])))
                            else:
                                row[fields[op['column']]['name']] = op['value']
                                if op['column'] == 0:
                                    locations[op['table'], op['value']] = receipt['row']
                    stage = d / (stem + '.mdb')
                    shutil.copyfile(current, stage)
                    for command in ('inspect', 'validate'):
                        args = [str(a.binary.resolve()), command, str(stage)] + (['--rows'] if command == 'inspect' else [])
                        check = subprocess.run(args, capture_output=True, text=True)
                        assert check.returncode == 0, (name, op, check.stderr)
                        (d / (stem + '.' + command + '.json')).write_text(check.stdout)
                    events.append({'ordinal': ordinal, 'operation': op, 'request': req, 'exit': r.returncode, 'before': before, 'stage': f'{name}/{stage.name}', 'image': ident(stage), 'expected_rows': copy.deepcopy(state)})
                assert ident(source) == original
                groups.append({'id': name, 'case': case, 'origin': origin, 'replica': replica, 'source': str(source), 'source_identity': original, 'initial': name + '/initial.mdb', 'events': events})
    result = {'document_type': 'cp1252_name_mutation_preparation', 'source_revision': a.source_revision, 'binary': ident(a.binary), 'groups': groups, 'status': 'accepted'}
    (a.output / 'matrix.json').write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    with zipfile.ZipFile(a.output / 'inputs.zip', 'x', compression=zipfile.ZIP_STORED) as z:
        z.write(a.output / 'matrix.json', 'matrix.json')
        for g in groups:
            z.write(a.output / g['initial'], g['initial'])
            for e in g['events']:
                z.write(a.output / e['stage'], e['stage'])
    print(json.dumps({'groups': len(groups), 'stages': sum((len(g['events']) for g in groups)), 'refusals': sum((e['exit'] != 0 for g in groups for e in g['events']))}))
if __name__ == '__main__':
    main()
