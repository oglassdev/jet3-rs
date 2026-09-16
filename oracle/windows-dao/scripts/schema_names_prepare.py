#!/usr/bin/env python3
"""Generate CP1252 schema candidates through the public JSON CLI."""
import argparse, hashlib, json, shutil, subprocess, zipfile
from pathlib import Path

def identity(p):
    data = p.read_bytes()
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

def request(case):
    out = {'tables': []}
    for table in case['tables']:
        item = {'name': table['name'], 'columns': [], 'indexes': [], 'rows': []}
        for field in table['fields']:
            c = {'name': field['name'], 'type': {4: 'long', 10: 'text', 12: 'memo'}[field['type']]}
            if field['type'] == 10:
                c['size'] = field['size']
            if field['allow_zero_length']:
                c['allow_zero_length'] = True
            item['columns'].append(c)
        for index in table['indexes']:
            item['indexes'].append({'name': index['name'], 'kind': 'primary' if index['primary'] else 'unique' if index['unique'] else 'ordinary', 'fields': [{'column': index['field']}]})
        for row in table['rows']:
            cells = []
            for field in table['fields']:
                value = row[field['name']]
                cells.append(None if value is None else {4: {'long': value}, 10: {'text': list(str(value).encode('cp1252'))}, 12: {'memo': list(str(value).encode('cp1252'))}}[field['type']])
            item['rows'].append(cells)
        out['tables'].append(item)
    if case['relations']:
        out['relationships'] = [{'name': r['name'], 'parent': {'table': r['parent'], 'column': r['parent_field']}, 'child': {'table': r['child'], 'column': r['child_field']}} for r in case['relations']]
    return out

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--matrix', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.matrix, args.output / 'matrix.json')
    matrix = json.loads(args.matrix.read_text())
    items = []
    for case in matrix['accepted']:
        req = args.output / (case['id'] + '.json')
        req.write_text(json.dumps(request(case), ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        files = []
        for replica in range(1, matrix['replicas'] + 1):
            path = args.output / f"r{replica}-candidate-{case['id']}.mdb"
            run = subprocess.run([str(args.binary.resolve()), 'create', str(path), '--input', str(req)], capture_output=True, text=True)
            (args.output / (path.stem + '.stdout')).write_text(run.stdout)
            (args.output / (path.stem + '.stderr')).write_text(run.stderr)
            if run.returncode:
                raise RuntimeError(f'{path.name}: {run.stderr}')
            check = subprocess.run([str(args.binary.resolve()), 'validate', str(path)], capture_output=True, text=True)
            (args.output / (path.stem + '.validate.stdout')).write_text(check.stdout)
            (args.output / (path.stem + '.validate.stderr')).write_text(check.stderr)
            if check.returncode:
                raise RuntimeError(f'{path.name} validation: {check.stderr}')
            files.append({'name': path.name, **identity(path), 'validation': json.loads(check.stdout)})
        assert files[0]['sha256'] == files[1]['sha256']
        items.append({'case': case['id'], 'request': identity(req), 'files': files})
    with zipfile.ZipFile(args.output / 'inputs.zip', 'x', compression=zipfile.ZIP_STORED) as z:
        z.write(args.output / 'matrix.json', 'matrix.json')
        for item in items:
            for f in item['files']:
                z.write(args.output / f['name'], 'candidates/' + f['name'])
    result = {'source_revision': args.source_revision, 'binary': identity(args.binary), 'matrix': identity(args.matrix), 'status': 'accepted', 'cases': items, 'bundle': identity(args.output / 'inputs.zip')}
    (args.output / 'preparation.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'accepted', 'groups': len(items), 'candidates': sum((len(i['files']) for i in items))}))
if __name__ == '__main__':
    main()
