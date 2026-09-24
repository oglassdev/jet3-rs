#!/usr/bin/env python3
"""Complete six-locale raw/DAO comparison, including exact native refusal residue."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path

import schema_edit_structure as raw
from compare_schema_structure import differences, semantics, preserve
from compare_schema_candidates import mask_dates
from locale_keys import Model, CONTEXTS
from locale_updates import host


def setup(keys):
    raw.setup(Path(__file__).resolve().parent)
    model = Model(json.loads(keys.read_text()))
    scalar = raw.index_key
    def index_key(value, column, descending):
        if column['type_code'] != 10 or value is None:
            return scalar(value, column, descending)
        value = bytes.fromhex(value['raw_hex']) if isinstance(value, dict) else value.encode('cp1252')
        encoded = model.encode(CONTEXTS[column['context_hex']], value)
        return bytes(b ^ 255 for b in encoded) if descending else encoded
    raw.index_key = index_key


def native_spec(spec):
    cp = 'cp' + str(spec['code_page'])
    def convert(value):
        if isinstance(value, str):
            return host(value.encode(cp))
        if isinstance(value, list):
            return [convert(v) for v in value]
        if isinstance(value, dict):
            return {k: convert(v) for k,v in value.items()}
        return value
    result = copy.deepcopy(spec)
    for step in result['steps']:
        step['request'] = convert(step['request'])
    # DAO 3.6 exposes BSTR names through the host ACP; requests use the DB page.
    return result


def compare_maps(name, left, right):
    suffix = name.split('-', 1)[1]
    roles = {
        'rows': {'Items/index/1/owned': (4,4), 'Items/index/2/owned': (4,5),
                 'Items/index/3/owned': (4,5), 'Items/table/available': (2,1)},
        'indexes': {'Items/index/4/owned': (3,3)},
        'table': {'RenamedÁ/index/0/owned': (1,1), 'RenamedÁ/index/1/owned': (1,1),
                  'RenamedÁ/table/available': (0,0)},
        'replace-index': {'Items/index/3/owned': (3,3)},
    }.get(suffix, {})
    raw.require(set(left) == set(right), 'complete allocation role inventory')
    placements = []
    for role, a in left.items():
        b = right[role]
        if a == b:
            continue
        raw.require(role == 'global' or role in roles, 'unlisted placement: ' + role)
        for key in ('kind','length','start','references'):
            raw.require(a['record'][key] == b['record'][key], 'map framing: ' + role + '/' + key)
        if role != 'global':
            raw.require((len(a['members']),len(b['members'])) == roles[role], 'recorded capacity: ' + role)
        placements.append(dict(role=role,candidate=a,native=b))
    return placements


def refusal(before, candidate, native, observed, spec):
    suffix = spec['name'].split('-', 1)[1]
    if suffix not in ('duplicate','orphan'):
        return None
    table = 'Items' if suffix == 'duplicate' else 'C'
    raw.require(candidate == before, 'byte-exact Rust refusal')
    index = observed['tables'][table]['definition']['physical_indexes'][0]
    offset = index['entry_count_offset']
    expected = bytearray(before)
    raw.require(expected[1538] == 0, 'native source header marker')
    expected[1538] = 1
    count = int.from_bytes(before[offset:offset+4], 'little')
    expected[offset:offset+4] = (count + 1).to_bytes(4, 'little')
    raw.require(native == expected, 'exact native primary-count/header refusal residue')
    return dict(table=table,entry_count_offset=offset,before=count,native=count+1,
                changed_bytes=[dict(offset=i,before=a,native=b) for i,(a,b) in enumerate(zip(before,native)) if a!=b])


def dao_compare(spec, images, files, residue):
    snapshots = []
    for kind in ('candidate', 'native'):
        item = copy.deepcopy(files[f"{kind}-{spec['name']}.mdb"])
        raw.require(item.pop('identity') == dict(size=len(images[kind]),sha256=raw.sha(images[kind])), 'DAO readback identity')
        item.pop('file')
        snapshots.append(mask_dates(item, set(spec.get('normalize_table_dates', []))))
    if residue:
        tables = []
        for snapshot in snapshots:
            values = snapshot['tables']
            if len(values) == 1 and isinstance(values[0], list):
                values = values[0]
            table, = [t for t in values if t['name'] == residue['table']]
            tables.append(table)
        counts = [[p for ix in table['indexes'] if ix['primary'] for p in ix['properties'] if p['name'] == 'DistinctCount'] for table in tables]
        raw.require(all(len(v) == 1 for v in counts), 'one primary distinct-count getter')
        raw.require([counts[0][0]['value'],counts[1][0]['value']] == [residue['before'],residue['native']], 'native getter matches exact raw residue')
        counts[1][0]['value'] = counts[0][0]['value']
    diff = differences(*snapshots)
    raw.require(not diff, 'complete DAO comparison: ' + str(diff[:5]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--keys',type=Path,required=True)
    p.add_argument('--prepared',type=Path,required=True)
    p.add_argument('--readback',type=Path)
    p.add_argument('--out',type=Path,required=True)
    args = p.parse_args()
    setup(args.keys)
    args.out.mkdir()
    manifest = json.loads((args.prepared/'manifest.json').read_text())
    files = None
    if args.readback:
        rb = json.loads(args.readback.read_text())
        raw.require(rb['status'] == 'pass', 'DAO observer status')
        files = {f['file']:f for f in rb['files']}
        expected = {f"{kind}-{case['name']}.mdb" for case in manifest['cases'] for kind in ('candidate','native')}
        raw.require(set(files) == expected and len(files) == len(rb['files']), 'exact DAO readback inventory')
    results, failures = [], []
    for original in manifest['cases']:
        spec = native_spec(original)
        name = spec['name']
        try:
            paths = {'input':Path(spec['input']), **{kind:args.prepared/f'{kind}-{name}.mdb' for kind in ('candidate','native')}}
            images = {kind:path.read_bytes() for kind,path in paths.items()}
            observed = {kind:raw.observe(path) for kind,path in paths.items()}
            for kind, value in observed.items():
                (args.out/f'{kind}-{name}.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
            residue = refusal(images['input'],images['candidate'],images['native'],observed['input'],spec)
            right = observed['input'] if residue else observed['native']
            diff = differences(semantics(observed['candidate'],spec),semantics(right,spec))
            raw.require(not diff, 'complete raw semantics: ' + str([(p,str(a)[:100],str(b)[:100]) for p,a,b in diff[:5]]))
            placements = compare_maps(name,observed['candidate']['maps'],observed['native']['maps'])
            preservation = preserve(observed['input'],observed['candidate'],images['input'],images['candidate'],spec)
            if files is not None:
                dao_compare(spec,images,files,residue)
            results.append(dict(name=name,placement_differences=placements,preservation=preservation,native_refusal_residue=residue))
        except Exception as error:
            failures.append(dict(name=name,error=str(error)))
    report = dict(status='fail' if failures else 'pass',pairs=len(results),results=results,failures=failures)
    (args.out/'REPORT.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    if failures:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
