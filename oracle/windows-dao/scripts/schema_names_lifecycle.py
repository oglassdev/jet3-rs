#!/usr/bin/env python3
"""Compare complete CP1252 name lifecycle captures and physical structures."""
import argparse, copy, hashlib, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
import system_catalog as catalog
import schema_generalization as schema
import numeric_index_mutation_structure as indexes
import relationship_mutation_structure as structure
catalog.MAX_TABLES = 64
catalog.MAX_PAGES = 8192
catalog.MAX_ROWS_PER_PAGE = 1019

def req(v, m):
    if not v:
        raise AssertionError(m)

def ident(p):
    d = p.read_bytes()
    return {'size': len(d), 'sha256': hashlib.sha256(d).hexdigest()}

def stable_snapshot(s):
    out = copy.deepcopy(s)

    def walk(v):
        if isinstance(v, dict):
            if set(('name', 'value')) <= set(v) and isinstance(v.get('name'), dict) and (v['name'].get('value') in ('DateCreated', 'LastUpdated')):
                v['value'] = '<DATE>'
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(out)
    return out

def seek_check(table, label):
    for name, read in table['index_reads'].items():
        req(read['error'] is None, label + ' read ' + name)
        tr = read['traversal']
        field = read['field']
        seen = []
        for row in tr:
            if row[field] not in seen:
                seen.append(row[field])
        req(len(read['seek']) == len(seen) + 1, label + ' Seek inventory ' + name)
        for item in read['seek']:
            want = next((r for r in tr if r[field] == item['query']), None)
            req(item['no_match'] == (want is None) and item['row'] == want, label + ' complete Seek ' + name)

def norm(v):
    return v.hex() if isinstance(v, bytes) else v.encode('cp1252').hex() if isinstance(v, str) else v

def expected_rows(case, event):
    specs = {t['name']: {f['name']: f for f in t['fields']} for t in case['tables']}
    out = {}
    for tn, rows in event['expected_rows'].items():
        out[tn] = [{k: v if specs[tn][k]['type'] == 4 or v is None else v.encode('cp1252').hex() for k, v in r.items()} for r in rows]
    return out

def map_info(data, loc, label):
    raw = catalog._locator_row(data, loc, label)
    members = sorted(catalog._locator_pages(data, loc, label))
    return {'locator': loc, 'raw_hex': raw.hex(), 'members': members}

def observe(path, capture, case, event, label):
    req(capture['before'] == capture['after'] == ident(path), label + ' read identity')
    snap = capture['snapshot']
    dao = {t['name']['value']: t for t in snap['tables']}
    req(set(dao) == {t['name'] for t in case['tables']}, label + ' DAO table inventory')
    want = expected_rows(case, event)
    for t in case['tables']:
        got = dao[t['name']]
        req(got['rows'] == want[t['name']], label + ' complete rows ' + t['name'])
        seek_check(got, label + '/' + t['name'])
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    named = {t['name']: t for t in analysis['tables'].values()}
    req(set(dao) <= set(named), label + ' raw tables')
    physical = {}
    rawrows = {}
    maps = {}
    schema_sig = {}
    typecode = {'Long': 4, 'Text': 10}
    for tn in dao:
        t = named[tn]
        rows = structure.rows(data, t, primary_column=t['definition']['columns'][0]['name'])
        got = [{k: norm(v) for k, v in r['values'].items()} for r in rows]
        req(got == want[tn], label + ' raw complete rows ' + tn)
        rawrows[tn] = [{'locator': r['locator'], 'storage': r['storage'], 'values': got[i], 'raw_hex': r['raw_hex'], 'descriptors': r['descriptors']} for i, r in enumerate(rows)]
        primary = t['definition']['columns'][0]['name']
        byloc = {(r['locator']['page'], r['locator']['row']): r['values'][primary] for r in rows}
        d = t['definition']
        items = []
        for ix in d['physical_indexes']:
            fields = []
            for key in ix['keys']:
                col = d['columns'][key['column']]
                req(col['type'] in typecode, label + ' supported indexed type')
                fields.append((typecode[col['type']], key['direction'] != 1))
            nodes, entries = indexes.tree(data, ix['root'], d['root'], fields)
            normalized = []
            for entry in entries:
                loc = (int.from_bytes(entry[-4:-1], 'big'), entry[-1])
                req(loc in byloc, label + ' index locator')
                normalized.append({'key_hex': entry[:-4].hex(), 'id': byloc[loc], 'locator': {'page': loc[0], 'row': loc[1]}})
            aliases = []
            for logical in d['logical_indexes']:
                physical_ordinal = structure.logical_relation(logical['raw_hex'])['physical_index'] if logical['class'] == 2 else logical['physical_index']
                if physical_ordinal == ix['index']:
                    aliases.append(logical['name'])
            items.append({'ordinal': ix['index'], 'aliases': aliases, 'flags': ix['flags'], 'first_word': int.from_bytes(bytes.fromhex(ix['prefix_hex']), 'little'), 'second_word': ix['entry_count'], 'entry_count_offset': ix['entry_count_offset'], 'root': ix['root'], 'nodes': [n['page'] for n in nodes], 'entries': normalized, 'map': map_info(data, ix['map'], label + ' index map')})
        physical[tn] = items
        for role, loc in d['maps'].items():
            maps[f'{tn}/table/{role}'] = map_info(data, loc, label + ' map')
        for ix in d['physical_indexes']:
            maps[f"{tn}/index/{ix['index']}"] = map_info(data, ix['map'], label + ' index map')
        for g in d['long_value_maps']:
            for role in ('owned', 'available'):
                maps[f"{tn}/lval/{g['column_name']}/{role}"] = map_info(data, g[role], label + ' lval map')
        schema_sig[tn] = {'root': d['root'], 'columns': d['columns'], 'logical_indexes': d['logical_indexes'], 'physical_indexes': [{k: v for k, v in x.items() if k not in ('entry_count', 'entry_count_offset', 'prefix_hex')} for x in d['physical_indexes']], 'maps': d['maps'], 'long_value_maps': d['long_value_maps']}
    syspages = set()
    for t in named.values():
        if not t['name'].startswith('MSys'):
            continue
        d = t['definition']
        for loc in list(d['maps'].values()) + [x['map'] for x in d['physical_indexes']] + [g[r] for g in d['long_value_maps'] for r in ('owned', 'available')]:
            syspages.update(catalog._locator_pages(data, loc, label + ' system maps'))
    system_hashes = {str(p): hashlib.sha256(data[p * 2048:(p + 1) * 2048]).hexdigest() for p in sorted(syspages)}
    props = schema.analyze_long_values(data, analysis)
    keys = schema.catalog_name_keys(data)
    return {'identity': ident(path), 'snapshot': snap, 'raw_rows': rawrows, 'physical': physical, 'maps': maps, 'schema': schema_sig, 'system_page_hashes': system_hashes, 'properties': props, 'catalog_keys': keys, 'page0_relationship_byte': data[1538]}

def physical_semantic(obs):
    return {tn: [{'ordinal': x['ordinal'], 'aliases': x['aliases'], 'flags': x['flags'], 'entries': [{k: v for k, v in e.items() if k != 'locator'} for e in x['entries']]} for x in xs] for tn, xs in obs['physical'].items()}

def prefixes(obs):
    return {tn: [[x['first_word'], x['second_word']] for x in xs] for tn, xs in obs['physical'].items()}

def relation_physical(case, obs):
    r = case['relations'][0]
    xs = obs['physical'][r['child']]
    hits = [x['ordinal'] for x in xs if r['name'] in x['aliases']]
    req(len(hits) == 1, 'foreign physical selector')
    return (r['child'], hits[0])

def diff_offsets(a, b):
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y] if len(a) == len(b) else [-1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('matrix', type=Path)
    ap.add_argument('outbox', type=Path)
    ap.add_argument('final_verification', type=Path)
    ap.add_argument('report', type=Path)
    a = ap.parse_args()
    m = json.loads(a.matrix.read_text())
    fv = json.loads(a.final_verification.read_text())
    req(len(m['groups']) == 8 and sum((len(g['events']) for g in m['groups'])) == 48, 'matrix inventory')
    expected = {*(g['id'] + '-result.json' for g in m['groups']), *(g['id'] + '-' + e['stage'].split('/')[-1][:-4] + '-' + role + '.mdb' for g in m['groups'] for e in g['events'] for role in ('native', 'rust')), 'workers.json', 'log.txt', 'exit.txt'}
    req({p.name for p in a.outbox.iterdir() if p.is_file()} == expected, 'exact output inventory')
    req((a.outbox / 'exit.txt').read_text().strip() == '0', 'wrapper exit')
    workers = json.loads((a.outbox / 'workers.json').read_text())
    req(len(workers['workers']) == 8 and all((x['exit_code'] == 0 and x['result'] for x in workers['workers'])), 'workers')
    report_groups = []
    env = None
    successes = refusals = 0
    for group in m['groups']:
        rec = json.loads((a.outbox / f"{group['id']}-result.json").read_text())
        req(rec['status'] == 'pass' and rec['error'] is None and (len(rec['events']) == len(group['events'])), 'receipt status/inventory')
        req(rec['archive'] == workers['archive'] and rec['config'] == workers['config'] and (rec['matrix'] == workers['matrix']), 'receipt linkage')
        env = rec['environment'] if env is None else env
        req(rec['environment'] == env, 'provider replication')
        prev_native = prev_rust = None
        events = []
        for spec, ev in zip(group['events'], rec['events']):
            req(ev['ordinal'] == spec['ordinal'] and ev['expected_exit'] == spec['exit'] and (ev['operation'] == spec['operation']), 'event request identity')
            rp = a.outbox / ev['rust_file']
            np = a.outbox / ev['native_file']
            req(ident(rp) == spec['image'] == ev['rust_pin'], 'Rust stage pin')
            ro = observe(rp, ev['rust_capture'], group['case'], spec, group['id'] + '/rust/' + ev['label'])
            no = observe(np, ev['native_capture'], group['case'], spec, group['id'] + '/native/' + ev['label'])
            req(stable_snapshot(ro['snapshot']) == stable_snapshot(no['snapshot']), 'complete semantic differential ' + group['id'] + '/' + ev['label'])
            req(ro['schema'] == no['schema'], 'schema/root/map-locator preservation')
            req(ro['maps'] == no['maps'], 'complete allocation-map equality')
            req(ro['system_page_hashes'] == no['system_page_hashes'], 'system owned pages exact')
            req(ro['properties'] == no['properties'], 'property payload preservation')
            req([{k: v for k, v in x.items() if k not in ('id', 'row_page', 'row_slot')} for x in ro['catalog_keys']] == [{k: v for k, v in x.items() if k not in ('id', 'row_page', 'row_slot')} for x in no['catalog_keys']], 'catalog keys')
            req(physical_semantic(ro) == physical_semantic(no), 'physical key/id inventory')
            if spec['exit'] == 0:
                successes += 1
                req(ev['native_error'] is None, 'unexpected native error')
                req(prefixes(ro) == prefixes(no), 'successful prefix equality')
            else:
                refusals += 1
                req(ev['native_error'] is not None, 'missing native refusal')
                number = 3201 if spec['operation']['operation'] == 'update' else 3200
                req(number in ev['native_error']['numbers'], 'native refusal number')
                req(spec['image'] == spec['before'], 'Rust refusal image exact')
                before = (a.outbox / rec['events'][spec['ordinal'] - 2]['native_file']).read_bytes() if spec['ordinal'] > 1 else None
                after = np.read_bytes()
                req(before is not None, 'refusal has preceding native stage')
                if number == 3200:
                    req(ev['before'] == ev['after'] and before == after, 'referenced-parent refusal exact native preservation')
                else:
                    tn, ix = relation_physical(group['case'], prev_native)
                    want = copy.deepcopy(prefixes(prev_native))
                    first, second = want[tn][ix]
                    want[tn][ix] = [first - 1, min(second, first - 1)] if first > 0 else [first, second]
                    req(prefixes(no) == want, 'orphan prefix finite side effect')
                    req(prefixes(ro) == prefixes(prev_rust), 'Rust refusal prefix preservation')
                    off = prev_native['physical'][tn][ix]['entry_count_offset']
                    allowed = {1538, *range(off - 4, off + 4)}
                    req(set(diff_offsets(before, after)) <= allowed, 'orphan refusal byte-diff scope')
            events.append({'ordinal': spec['ordinal'], 'label': ev['label'], 'expected_exit': spec['exit'], 'native_error': ev['native_error'], 'native_before': ev['before'], 'native_after': ev['after'], 'rust': {k: ro[k] for k in ('identity', 'physical', 'raw_rows', 'maps', 'schema', 'page0_relationship_byte')}, 'native': {k: no[k] for k in ('identity', 'physical', 'raw_rows', 'maps', 'schema', 'page0_relationship_byte')}})
            prev_native = no
            prev_rust = ro
        report_groups.append({'id': group['id'], 'origin': group['origin'], 'case': group['case']['id'], 'replica': group['replica'], 'events': events})
    req(successes == 40 and refusals == 8, 'operation totals')
    req(all((x['byte_exact'] for x in fv['checks'])), 'final equivalence')
    report = {'document_type': 'dao_cp1252_schema_name_lifecycle_acceptance', 'status': 'accepted', 'source_revision': fv['source_revision'], 'binary_sha256': fv['binary_sha256'], 'source_archive_sha256': fv['source_archive_sha256'], 'environment': env, 'matrix': ident(a.matrix), 'producer': ident(HERE / 'schema_name_lifecycle.ps1'), 'evaluator': ident(Path(__file__)), 'final_source_verification': ident(a.final_verification), 'coverage': {'groups': 8, 'stages': 48, 'successful_operations': successes, 'refusals': refusals, 'captures': 96}, 'groups': report_groups}
    a.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(a.report)
if __name__ == '__main__':
    main()
