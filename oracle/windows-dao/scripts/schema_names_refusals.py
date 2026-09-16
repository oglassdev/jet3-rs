#!/usr/bin/env python3
"""Exercise CLI name rejection and verify no destination or extra files appear."""
import argparse, hashlib, json, subprocess
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('binary', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
a.output.mkdir(exist_ok=False)
base = {'name': 'Items', 'columns': [{'name': 'Id', 'type': 'long'}], 'indexes': []}
invalid = ['', ' lead', 'Bad\x00Name', 'Bad\tName', 'Bad\x7fName', 'Bad.Name', 'Bad!Name', 'Bad[Name', 'Bad]Name', 'Bad`Name', 'Bad\x81Name', '中文', '🙂']
collisions = [['Case', 'case'], ['AE', 'Æ'], ['ss', 'ß'], ['Café', 'CAFÉ'], ["Q'", 'Q’']]
records = []
for role in ['table', 'column', 'index']:
    for j, names in enumerate([[n] for n in invalid] + [['x' * (64 if role == 'index' else 65)]] + collisions):
        t = json.loads(json.dumps(base))
        req = {'tables': [t]}
        if role == 'table':
            req['tables'] = [dict(t, name=n) for n in names]
        elif role == 'column':
            t['columns'] = [{'name': n, 'type': 'long'} for n in names]
        else:
            t['indexes'] = [{'name': n, 'kind': 'ordinary', 'fields': [{'column': 'Id'}]} for n in names]
        name = f'{role}-{j:02d}'
        file = a.output / (name + '.json')
        file.write_text(json.dumps(req, ensure_ascii=False, sort_keys=True) + '\n')
        dest = a.output / (name + '.mdb')
        run = subprocess.run([str(a.binary.resolve()), 'create', str(dest), '--input', str(file)], capture_output=True, text=True)
        (a.output / (name + '.stdout')).write_text(run.stdout)
        (a.output / (name + '.stderr')).write_text(run.stderr)
        assert run.returncode != 0 and (not dest.exists()), (name, run.stdout, run.stderr)
        records.append({'case': name, 'names': names, 'exit': run.returncode, 'destination_absent': True})
expected = {'report.json'} | {f"{r['case']}.{suffix}" for r in records for suffix in ('json', 'stdout', 'stderr')}
assert {p.name for p in a.output.iterdir()} == expected - {'report.json'}
result = {'binary_sha256': hashlib.sha256(a.binary.read_bytes()).hexdigest(), 'status': 'accepted', 'checks': records}
(a.output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
print(json.dumps({'status': 'accepted', 'refusals': len(records)}))
