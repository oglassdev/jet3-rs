#!/usr/bin/env python3
"""Finite EXP-0241 native catalog page/index growth discovery."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

from creation_tables import identity, normalized, require, write
import multi_level_index_structure

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'catalog-pages-native.json'
catalog = multi_level_index_structure.catalog
# An isolated decoder instance extends only the admitted record shape. Its
# EXP-0062 links/prefixes and EXP-0225 fence checks remain unchanged.
_spec = importlib.util.spec_from_file_location('catalog_pages_tree', SCRIPT.with_name('numeric_index_mutation_structure.py'))
index = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(index)
LETTERS = bytes.fromhex('60 61 62 64 66 67 68 69 6a 6b 6c 6d 6f 70 72 73 74 75 76 77 78 7a 7b 7c 7d 7e')


def record_width(record, fields):
    require(record[0] == 127, 'Present catalog Long key')
    if fields == 'long': return 9
    require(fields == 'name' and record[5] == 127, 'Present catalog name component')
    end = record.index(0, 6)
    require(all(b >= 16 for b in record[6:end]), 'ASCII primary weights without secondary nibbles')
    return end + 1 + 4


index.record_width = record_width


def long_key(value): return b'\x7f' + ((value & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')


def name_key(parent, name):
    def weight(c):
        if 'a' <= c <= 'z': return LETTERS[ord(c) - 97]
        if '0' <= c <= '9': return 0x56 + ord(c) - 48
        raise ValueError('Name outside finite letter/digit inventory')
    return long_key(parent) + b'\x7f' + bytes(weight(c) for c in name.lower()) + b'\x00'


def recipe():
    return [dict(name=name, count=count, width=width, name_width=name_width,
                 tables=[dict(name=f'T{n:03}' + 'x' * (name_width - 4), columns=[f'C{c:02}' for c in range(width)], indexes=[], rows=[]) for n in range(count)])
            for name, count, width, name_width in [('short40', 40, 1, 4), ('short110', 110, 1, 4),
                                                   ('long30-wide', 30, 32, 48), ('long40', 40, 3, 48)]]


def sources():
    return [Path(__file__), SCRIPT, SCRIPT.with_name('creation_tables.py'), SCRIPT.with_name('creation_tables.ps1'),
            SCRIPT.with_name('field_update.ps1'), SCRIPT.with_name('numeric_index_mutation_structure.py'),
            Path(catalog.__file__), Path(multi_level_index_structure.__file__)]


def prepare(directory, revision):
    directory.mkdir(parents=True)
    for path in [SCRIPT, SCRIPT.with_name('creation_tables.ps1'), SCRIPT.with_name('field_update.ps1')]: shutil.copy2(path, directory / path.name)
    manifest = dict(document_type='catalog_pages_native_inputs', source_revision=revision, cases=recipe(),
                    files={p.name: identity(p) for p in directory.iterdir() if p.is_file()},
                    sources={str(p.relative_to(ROOT)): identity(p) for p in sources()})
    write(directory / MANIFEST, manifest)
    return manifest


def physical_row(data, page, slot):
    image = catalog._page(data, page, 'row')
    entry = next((r for r in catalog._row_directory(image, page) if r['row'] == slot), None)
    require(entry is not None, 'Physical row locator')
    return entry, image[entry['start']:entry['end']]


def payload(data, field, raw):
    header = bytes.fromhex(field['long_value_header_hex'])
    require(len(header) == 12, 'Long-value header')
    control = int.from_bytes(header[:4], 'little'); length = control & 0xffffff; flags = control & ~0xffffff
    if flags == 0x80000000:
        start = raw.index(header) + 12; result = raw[start:start + field['inline_length'] - 12]
    else:
        require(flags in (0, 0x40000000), 'Known catalog long-value storage')
        page, slot = int.from_bytes(header[5:8], 'little'), header[4]; result = b''; seen = set()
        while page:
            require((page, slot) not in seen, 'Acyclic catalog payload'); seen.add((page, slot))
            entry, fragment = physical_row(data, page, slot)
            require(data[page * 2048 + 4:page * 2048 + 8] == b'LVAL' and not entry['hidden'] and not entry['overflow'], 'Live catalog payload target')
            if flags == 0x40000000: result += fragment; break
            require(len(fragment) >= 4, 'Chained payload pointer'); result += fragment[4:]
            page, slot = int.from_bytes(fragment[1:4], 'little'), fragment[0]
    require(len(result) == length, 'Complete catalog payload')
    return result


def rows(data, table, pages):
    result = []; hidden_targets = set()
    for page in pages:
        for entry in catalog._row_directory(catalog._page(data, page, 'table'), page):
            if entry['hidden']: continue
            current = (page, entry['row']); seen = set(); chain = []
            while True:
                require(current not in seen and len(seen) < 32, 'Catalog overflow chain'); seen.add(current)
                stored, raw = physical_row(data, *current)
                require(current[0] in pages and int.from_bytes(data[current[0] * 2048 + 4:current[0] * 2048 + 8], 'little') == table['root'], 'Catalog overflow owner')
                if current != (page, entry['row']):
                    require(stored['hidden'], 'Hidden overflow storage'); hidden_targets.add(current)
                chain.append(list(current))
                if not stored['overflow']: break
                require(len(raw) == 4, 'Overflow locator width'); current = (int.from_bytes(raw[1:4], 'little'), raw[0])
            decoded = catalog._decode_row(raw, table['columns'], 'catalog row')
            for n, value in enumerate(decoded['values']):
                if isinstance(value, dict) and 'long_value_header_hex' in value:
                    full = payload(data, value, raw)
                    decoded['values'][n] = full.decode('cp1252') if table['columns'][n]['type'] == 'Memo' else full.hex()
            result.append(dict(page=page, row=entry['row'], storage=chain, values=decoded['values']))
    hidden = {(p, r['row']) for p in pages for r in catalog._row_directory(catalog._page(data, p, 'table'), p) if r['hidden'] and r['end'] > r['start']}
    require(hidden == hidden_targets, 'Complete active hidden-row coverage')
    return result


def inspect(data):
    output = {}
    for root, name in [(2, 'MSysObjects'), (3, 'MSysACEs')]:
        table = catalog._definition(data, root); pages, lval = catalog._table_pages(data, table)
        require(not lval, 'System table owns only data pages')
        available = sorted(catalog._locator_pages(data, table['maps']['available'], name + ' available'))
        require(set(available) <= set(pages), 'Available subset of owned data')
        decoded = rows(data, table, pages); require(len(decoded) == table['row_count'], 'Declared system row count')
        item = dict(definition=table, rows=decoded, owned=pages, available=available, indexes=[],
                    pages=[dict(page=p, free=int.from_bytes(data[p * 2048 + 2:p * 2048 + 4], 'little'),
                                directory=catalog._row_directory(catalog._page(data, p, name), p)) for p in pages])
        for physical in table['physical_indexes']:
            name_shape = root == 2 and len(physical['keys']) == 2
            nodes, entries = index.tree(data, physical['root'], root, 'name' if name_shape else 'long')
            wanted = sorted((name_key(r['values'][1], r['values'][2]) if name_shape else long_key(r['values'][0])) + r['page'].to_bytes(3, 'big') + bytes([r['row']]) for r in decoded)
            require(entries == wanted, 'Complete system index key/locator multiset')
            owned = sorted(catalog._locator_pages(data, physical['map'], 'system index map'))
            require({n['page'] for n in nodes} <= set(owned), 'All index nodes owned')
            distinct = len({e[:-4] for e in entries}); require(physical['entry_count'] == distinct, 'Initial distinct-key counter')
            item['indexes'].append(dict(root=physical['root'], owned=owned, nodes=nodes, entries=[e.hex() for e in entries], distinct=distinct))
        output[name] = item
    return output


def row_multiset(values): return Counter(tuple(row) for row in values)


def compare_system(observation, raw):
    require(observation['status'] == 'pass' and observation['error'] is None, 'DAO system capture')
    require([t['name'] for t in observation['tables']] == ['MSysObjects', 'MSysACEs'], 'System inventory')
    for table in observation['tables']:
        decoded = raw[table['name']]; definition = decoded['definition']
        types = {'Boolean': 1, 'Integer': 3, 'Long': 4, 'Date': 8, 'Binary': 9, 'Text': 10, 'LongBinary': 11, 'Memo': 12}
        require([[f['name'], f['type'], f['size']] for f in table['fields']] == [[c['name'], types[c['type']], c['size']] for c in definition['columns']], 'System column schema')
        values = [r['values'] for r in decoded['rows']]
        require(row_multiset(values) == row_multiset(table['rows']), 'Full DAO/raw system row payloads')
        logical = {i['name']: i for i in definition['logical_indexes']}
        require(set(logical) == {i['name'] for i in table['indexes']}, 'System logical index inventory')
        for idx in table['indexes']:
            physical = definition['physical_indexes'][logical[idx['name']]['physical_index']]
            columns = [c['column'] for c in physical['keys']]
            require([f['name'] for f in idx['fields']] == [definition['columns'][c]['name'] for c in columns], 'System key fields')
            traversal = idx['traversal']; require(row_multiset(traversal) == row_multiset(values), 'Complete system index traversal')
            encoded = [name_key(r[1], r[2]) if len(columns) == 2 else long_key(r[0]) for r in traversal]
            require(encoded == sorted(encoded), 'System index traversal order')


def evaluate(directory, outbox):
    manifest_path = directory / MANIFEST; manifest = json.loads(manifest_path.read_text())
    result_path = outbox / 'result.json'; result = json.loads(result_path.read_text(encoding='utf-8-sig'))
    report = dict(document_type='catalog_pages_native_report', status='failed', manifest=identity(manifest_path), result=identity(result_path), environment=result['environment'], cases=[], error=None)
    try:
        for name, pin in manifest['sources'].items(): require(identity(ROOT / name) == pin, 'Source identity: ' + name)
        for name, pin in manifest['files'].items(): require(identity(directory / name) == pin, 'Input identity: ' + name)
        require(result['document_type'] == 'dao_catalog_pages_native_result' and result['manifest_sha256'] == report['manifest']['sha256'] and result['source_revision'] == manifest['source_revision'], 'Producer/source binding')
        require(result['error'] is None and result['retention_failures'] == [], 'Producer and retention')
        require(result['environment']['process_bits'] == 32 and result['environment']['provider_version'] == '3.6', 'Actual native provider')
        require([(c['name'], c['replica']) for c in result['cases']] == [(c['name'], r) for c in manifest['cases'] for r in (1, 2)], 'Case inventory')
        for outcome in result['cases']:
            checked = dict(name=outcome['name'], replica=outcome['replica'], status='failed', error=None); report['cases'].append(checked)
            try:
                case = next(c for c in manifest['cases'] if c['name'] == outcome['name'])
                require(outcome['status'] == 'pass', 'Capture failure: ' + str(outcome['error']))
                path = outbox / outcome['user']['file']; pin = identity(path)
                require(outcome['user']['before'] == outcome['user']['after'] == outcome['system']['before'] == outcome['system']['after'] == pin, 'Immutable native input identity')
                normalized(outcome['user']['snapshot'], case)
                raw = inspect(path.read_bytes()); compare_system(outcome['system'], raw)
                objects = raw['MSysObjects']; aces = raw['MSysACEs']
                require(len(objects['rows']) == 8 + case['count'] and len(aces['rows']) == 16 + 2 * case['count'], 'Per-create catalog/ACE cardinality')
                require({r['values'][2] for r in objects['rows'] if r['values'][3] == 1 and r['values'][7] == 0} == {t['name'] for t in case['tables']}, 'Complete user catalog objects')
                for row in objects['rows']:
                    if row['values'][3] != 1 or row['values'][7] != 0: continue
                    id = row['values'][0]; user = catalog._definition(path.read_bytes(), id)
                    require([c['name'] for c in user['columns']] == [f'C{c:02}' for c in range(case['width'])], 'Catalog Id equals user definition root')
                    permissions = [r['values'][1:] for r in aces['rows'] if r['values'][0] == id]
                    require(sorted(permissions) == [['0201', 1048319, False], ['0301', 983294, False]], 'Two non-inheritable user ACE rows')
                checked.update(status='accepted', file=path.name, identity=pin, file_pages=path.stat().st_size // 2048, layout=raw)
            except Exception as error: checked['error'] = str(error)
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more native cases failed')
        require(any(len(c['layout']['MSysACEs']['owned']) > 1 for c in report['cases']), 'Observed multi-page ACE data')
        require(any(c['layout']['MSysACEs']['indexes'][0]['nodes'][0]['children'] for c in report['cases']), 'Observed branched ACE index')
        require(any(c['layout']['MSysObjects']['indexes'][0]['nodes'][0]['children'] for c in report['cases']), 'Observed branched ParentIdName index')
        report['status'] = 'accepted'
    except Exception as error: report['error'] = str(error)
    target = outbox / 'catalog-pages-native-report.json'; number = 1
    while target.exists():
        number += 1; target = outbox / f'catalog-pages-native-report-{number}.json'
    write(target, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('directory', type=Path); p.add_argument('--revision', required=True)
    p = sub.add_parser('evaluate'); p.add_argument('directory', type=Path); p.add_argument('outbox', type=Path)
    args = parser.parse_args(); result = prepare(args.directory, args.revision) if args.command == 'prepare' else evaluate(args.directory, args.outbox)
    print(json.dumps(dict(status=result.get('status', 'prepared'), cases=len(result['cases']))))
    if result.get('status') == 'failed': raise SystemExit(1)


if __name__ == '__main__': main()
