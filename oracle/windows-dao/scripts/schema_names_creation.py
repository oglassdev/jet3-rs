#!/usr/bin/env python3
"""Compare complete DAO creation captures for CP1252 schema names."""
import argparse, copy, hashlib, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
import system_catalog as catalog
import catalog_keys as schema
catalog.MAX_TABLES = 64

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

def seek_check(table):
    for name, read in table['index_reads'].items():
        req(read['error'] is None, 'index read error ' + table['name']['value'] + '/' + name)
        tr = read['traversal']
        field = read['field']
        seen = []
        for row in tr:
            if row[field] not in seen:
                seen.append(row[field])
        req(len(read['seek']) == len(seen) + 1, 'Seek inventory ' + name)
        for item in read['seek']:
            want = next((r for r in tr if r[field] == item['query']), None)
            req(item['no_match'] == (want is None) and item['row'] == want, 'complete Seek ' + name)

def expected_rows(t):
    return [{f['name']: row[f['name']] if f['type'] == 4 else row[f['name']].encode('cp1252').hex() for f in t['fields']} for row in t['rows']]

def expected_index_names(case, t):
    x = {i['name'] for i in t['indexes']}
    x |= {r['name'] for r in case['relations'] if r['child'] == t['name']}
    return x

def inspect_role(case, role, receipt, path):
    cap = receipt[role]['capture']
    req(cap['before'] == cap['after'] == ident(path), role + ' read-only identity ' + case['id'])
    snap = cap['snapshot']
    actual = {t['name']['value']: t for t in snap['tables']}
    req(set(actual) == {t['name'] for t in case['tables']}, role + ' table inventory ' + case['id'])
    for want in case['tables']:
        got = actual[want['name']]
        req(got['name']['utf16le'] == want['name'].encode('utf-16le').hex() and got['name']['cp1252'] == want['name'].encode('cp1252').hex(), 'table name encoding')
        req([f['name']['value'] for f in got['fields']] == [f['name'] for f in want['fields']], 'field names/order ' + want['name'])
        for gf, wf in zip(got['fields'], want['fields']):
            req((gf['type'], gf['size'], gf['allow_zero_length']) == (wf['type'], wf['size'], wf['allow_zero_length']), 'field schema ' + wf['name'])
            req(gf['name']['cp1252'] == wf['name'].encode('cp1252').hex(), 'field name encoding ' + wf['name'])
        req(got['rows'] == expected_rows(want), 'complete rows ' + want['name'])
        req({i['name']['value'] for i in got['indexes']} == expected_index_names(case, want), 'index inventory ' + want['name'])
        seek_check(got)
    expected_rel = [(r['name'], r['parent'], r['child'], r['parent_field'], r['child_field']) for r in case['relations']]
    got_rel = [(r['name']['value'], r['table']['value'], r['foreign_table']['value'], r['fields'][0]['name']['value'], r['fields'][0]['foreign_name']['value']) for r in snap['relations']]
    req(got_rel == expected_rel, role + ' relation inventory ' + case['id'])
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    keys = schema.catalog_name_keys(data)
    props = schema.analyze_long_values(data, analysis)
    definition, _, rows = catalog._discover_catalog(data)
    no = catalog._ordinal(definition, 'Name')
    io = catalog._ordinal(definition, 'Id')
    raw = {}
    for row in rows:
        name = row['values'][no]
        if name not in actual:
            continue
        td = catalog._definition(data, row['values'][io])
        pages, lvals = catalog._table_pages(data, td)
        decoded = catalog._table_rows(data, td, pages)
        req([c['name'] for c in td['columns']] == [f['name']['value'] for f in actual[name]['fields']], 'raw field names ' + name)
        raw_indexes = [i['name'] for i in td['logical_indexes']]
        dao_indexes = [i['name']['value'] for i in actual[name]['indexes']]
        parent_rels = [r for r in case['relations'] if r['parent'] == name]
        if parent_rels:
            hidden = [x for x in raw_indexes if x.startswith('.r')]
            visible = [x for x in raw_indexes if not x.startswith('.r')]
            req(hidden == ['.rB'] and visible == dao_indexes, 'hidden parent index role ' + name)
        else:
            req(raw_indexes == dao_indexes, 'raw logical order ' + name)
        req(len(decoded) == len(actual[name]['rows']), 'raw row inventory ' + name)
        raw[name] = {'root': td['root'], 'columns': [c['name'] for c in td['columns']], 'logical_indexes': raw_indexes, 'row_locators': [[r['page'], r['row']] for r in decoded]}
    pages = analysis['pages']
    page_summary = {k: len(v) if hasattr(v, '__len__') else v for k, v in pages.items()} if isinstance(pages, dict) else {'count': len(pages)}
    return {'file': path.name, 'image': ident(path), 'catalog_keys': keys, 'properties': props, 'raw_tables': raw, 'page_summary': page_summary, 'free_pages': len(analysis['free_pages']), 'structures': len(analysis['structures'])}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('matrix', type=Path)
    ap.add_argument('outbox', type=Path)
    ap.add_argument('config', type=Path)
    ap.add_argument('final_verification', type=Path)
    ap.add_argument('report', type=Path)
    a = ap.parse_args()
    m = json.loads(a.matrix.read_text())
    cfg = json.loads(a.config.read_text())
    fv = json.loads(a.final_verification.read_text())
    req(m['replicas'] == 2 and len(m['accepted']) == 6, 'matrix inventory')
    names = [c['id'] for c in m['accepted']]
    expected = {*(f'r{r}-{c}-result.json' for r in (1, 2) for c in names), *(f'r{r}-{role}-{c}.mdb' for r in (1, 2) for c in names for role in ('candidate', 'native')), 'workers.json', 'log.txt', 'exit.txt'}
    req({p.name for p in a.outbox.iterdir() if p.is_file()} == expected, 'exact output inventory')
    req((a.outbox / 'exit.txt').read_text().strip() == '0', 'wrapper exit')
    workers = json.loads((a.outbox / 'workers.json').read_text())
    req(len(workers['workers']) == 12 and all((x['exit_code'] == 0 and x['result'] for x in workers['workers'])), 'worker inventory/status')
    reports = []
    env = None
    for case in m['accepted']:
        for r in (1, 2):
            receipt = json.loads((a.outbox / f"r{r}-{case['id']}-result.json").read_text())
            req(receipt['status'] == 'pass' and receipt['error'] is None, 'receipt status')
            req(receipt['case'] == case['id'] and receipt['replica'] == r, 'receipt selector')
            req(receipt['archive'] == workers['archive'] and receipt['config'] == workers['config'] and (receipt['matrix'] == workers['matrix']), 'receipt linkage')
            if env is None:
                env = receipt['environment']
            req(receipt['environment'] == env, 'provider identity')
            cpath = a.outbox / f"r{r}-candidate-{case['id']}.mdb"
            npath = a.outbox / f"r{r}-native-{case['id']}.mdb"
            cs = inspect_role(case, 'candidate', receipt, cpath)
            ns = inspect_role(case, 'native', receipt, npath)
            req(stable_snapshot(receipt['candidate']['capture']['snapshot']) == stable_snapshot(receipt['native']['capture']['snapshot']), 'semantic pair after date-only normalization ' + case['id'])
            strip = lambda ks: [{k: v for k, v in x.items() if k not in ('id', 'row_page', 'row_slot')} for x in ks]
            req(strip(cs['catalog_keys']) == strip(ns['catalog_keys']), 'catalog key bytes/semantics ' + case['id'])
            reports.append({'case': case['id'], 'replica': r, 'candidate': cs, 'native': ns})
    req(all((x['byte_exact'] for x in fv['checks'])), 'all final generated files byte-exact')
    report = {'document_type': 'dao_cp1252_schema_name_creation_acceptance', 'status': 'accepted', 'source_revision': fv['source_revision'], 'source_archive_sha256': fv['source_archive_sha256'], 'binary_sha256': fv['binary_sha256'], 'reviewed_generation_binary_sha256': cfg['binary_sha256'], 'environment': env, 'matrix': ident(a.matrix), 'config': ident(a.config), 'producer': ident(HERE / 'schema_name_creation.ps1'), 'evaluator': ident(Path(__file__)), 'final_source_verification': ident(a.final_verification), 'coverage': {'groups': 6, 'replicas': 2, 'pairs': 12, 'mdbs': 24}, 'pairs': reports}
    a.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(a.report)
if __name__ == '__main__':
    main()
