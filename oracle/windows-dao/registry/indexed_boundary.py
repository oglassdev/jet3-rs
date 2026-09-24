"""Indexed insertion boundaries (EXP-0217/0218/0221).

Three arms insert into an Items table with one ascending Long primary index: `space` fits an
existing data page, `eof` appends exactly one data page, and `duplicate` must be refused with
the source unchanged. Every candidate byte is reconstructed independently; DAO then compares
the original, the Rust candidate and a native insertion on a copy of the original.
"""

from __future__ import annotations

import copy
from pathlib import Path

import structure
from registry import common
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'indexed_boundary.ps1'
MANIFEST = 'indexed-boundary.json'
ROLES = ('original', 'candidate', 'control')
TABLES = ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes']
CURRENCY = (-123456).to_bytes(8, 'little', signed=True)


def definition(data):
    table = common.tables(data, ['Items'])['Items']
    require(not table['long_value_pages'] and len(table['physical_indexes']) == len(table['logical_indexes']) == 1, 'One scalar index')
    return table, common.table_rows(data, table)


def values(key):
    return [key, 'x' * 80, None if key % 2 == 0 else '-12.3456', key % 2 != 0]


def patch_check(before, after, arm):
    """Reconstructs the complete candidate image from the original (EXP-0060/0061)."""
    table, rows = definition(before)
    require(len(rows) == table['row_count'] == arm['count'], 'Original count')
    if arm['name'] == 'duplicate':
        require(before == after, 'Refusal changed bytes')
        return dict(refusal=arm['name'], preserved=True)
    key = arm['id']
    # Fixed fields, variable text footer and Boolean presence.
    encoded = bytes([4]) + key.to_bytes(4, 'little', signed=True) + CURRENCY + b'x' * 80 + bytes([93, 13, 1, 15])
    require(key % 2 == 1, 'Finite present Currency insertion')
    expected = bytearray(before)
    pages = sorted(common.map_pages(before, table['maps']['owned']))
    eof = arm['name'] == 'eof'
    free_pages = []
    for page in pages:
        raw = structure.page(before, page, 'Items')
        directory = structure.directory(raw, page)
        require(directory and all(not e['hidden'] and not e['overflow'] for e in directory), 'Ordinary populated data')
        free = directory[-1]['start'] - 10 - 2 * len(directory)
        require(free == int.from_bytes(raw[2:4], 'little'), 'Free count')
        if free >= 2 * (len(encoded) + 2):
            free_pages.append(page)
        if eof:
            require(free < len(encoded) + 2, 'Physical boundary required')
    if eof:
        page, slot = len(before) // common.PAGE, 0
        require(len(after) == len(before) + common.PAGE, 'One appended page')
        for role, where, set_value in [('global', dict(page=1, row=0), False), ('owned', table['maps']['owned'], True),
                                       ('available', table['maps']['available'], True)]:
            raw = structure.locator_row(before, where, role)
            relative = page - int.from_bytes(raw[1:5], 'little')
            require(raw[0] == 0 and 0 <= relative < 8 * (len(raw) - 5), 'Inline map coverage')
            entry = structure.directory(structure.page(before, where['page'], role), where['page'])[where['row']]
            offset = where['page'] * common.PAGE + entry['start'] + 5 + relative // 8
            mask = 1 << (relative % 8)
            require(bool(before[offset] & mask) != set_value, 'Original EOF map bit')
            expected[offset] = expected[offset] | mask if set_value else expected[offset] & ~mask
        image = bytearray(common.PAGE)
        image[:2] = b'\x01\x01'
        image[2:4] = (2036 - len(encoded)).to_bytes(2, 'little')
        image[4:8] = table['root'].to_bytes(4, 'little')
        image[8:10] = b'\x01\x00'
        image[10:12] = (common.PAGE - len(encoded)).to_bytes(2, 'little')
        image[-len(encoded):] = encoded
        expected.extend(image)
    else:
        require(len(free_pages) == 1 and len(before) == len(after), 'One existing candidate')
        page = free_pages[0]
        base = page * common.PAGE
        directory = structure.directory(before[base:base + common.PAGE], page)
        slot = len(directory)
        high = directory[-1]['start']
        low = high - len(encoded)
        expected[base + low:base + high] = encoded
        expected[base + 10 + slot * 2:base + 12 + slot * 2] = low.to_bytes(2, 'little')
        expected[base + 8:base + 10] = (slot + 1).to_bytes(2, 'little')
        expected[base + 2:base + 4] = (low - 12 - slot * 2).to_bytes(2, 'little')
    count = len(rows) + 1
    root = table['root'] * common.PAGE
    for offset in (12, 47):
        expected[root + offset:root + offset + 4] = count.to_bytes(4, 'little')
    records = [(r['values'][0], r['page'], r['row']) for r in rows] + [(key, page, slot)]
    records = sorted(common.long_key(k) + common.locator_bytes(p, s) for k, p, s in records)
    leaf = table['physical_indexes'][0]['root'] * common.PAGE
    require(before[leaf:leaf + 2] == b'\x04\x01' and before[leaf + 8:leaf + 22] == bytes(14), 'Isolated uncompressed root leaf')
    expected[leaf + 2:leaf + 4] = (1800 - count * 9).to_bytes(2, 'little')
    bitmap = bytearray(226)
    for n in range(1, count + 1):
        bitmap[n * 9 // 8] |= 1 << (n * 9 % 8)
    expected[leaf + 22:leaf + 248] = bitmap
    expected[leaf + 248:leaf + 248 + count * 9] = b''.join(records)
    require(expected == after, 'Exact data/allocation/count/leaf patch and unrelated preservation')
    return dict(page=page, slot=slot, count=count, eof=eof, unrelated_bytes_preserved=True)


def expected(snapshot, arm, role):
    value = copy.deepcopy(snapshot)
    rows = [values(k) for k in range(arm['count'])]
    if role != 'original' and arm['name'] in ('space', 'eof'):
        rows.append(values(arm['id']))
    rows.sort()
    require(value['version'] == '3.0' and value['queries'] == value['relations'] == [] and value['tables'] == TABLES, 'Database inventory')
    require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'User inventory')
    items, notes = value['user_tables']
    items['rows'].sort()
    notes['rows'].sort()
    for row in items['rows'] + value['traversal'] + [s['row'] for s in value['seek'] if s['row'] is not None]:
        require(len(row) == 4 and type(row[0]) is int and type(row[1]) is str and (row[2] is None or type(row[2]) is str)
                and type(row[3]) is bool, 'Typed Items serialization')
    require(items['rows'] == rows and notes['rows'] == common.NOTES, 'Complete typed rows and Memo')
    for table, fields in [(items, [('Id', 4, 4), ('Name', 10, 80), ('Price', 5, 8), ('Active', 1, 1)]),
                          (notes, [('Id', 4, 4), ('Body', 12, 0)])]:
        require(table['attributes'] == 0 and [(f['name'], f['type'], f['size']) for f in table['fields']] == fields, 'Exact schema')
    require(notes['indexes'] == [] and items['indexes'] == [dict(name='ById', primary=True, unique=True, required=True, foreign=False,
                                                                 ignore_nulls=False, fields=[dict(name='Id', attributes=0)])],
            'Exact index metadata')
    require(value['traversal'] == rows and value['seek'] == [dict(query=k, row=next((r for r in rows if r[0] == k), None)) for k in range(-1, 202)],
            'Complete traversal and present/missing Seek')
    return value


def raw_check(data, arm, role):
    table, rows = definition(data)
    require([(c['name'], c['type'], c['size']) for c in table['columns']]
            == [('Id', 'Long', 4), ('Name', 'Text', 80), ('Price', 'Currency', 8), ('Active', 'Boolean', 1)], 'Raw schema')
    ids = list(range(arm['count']))
    if role != 'original' and arm['name'] in ('space', 'eof'):
        ids.append(arm['id'])
    expected_rows = [[k, 'x' * 80, None if k % 2 == 0 else dict(raw_hex=CURRENCY.hex()), k % 2 != 0] for k in sorted(ids)]
    require(sorted((r['values'] for r in rows), key=lambda r: r[0]) == expected_rows, 'Complete raw rows')
    physical = table['physical_indexes'][0]
    require(physical['flags'] == 9 and physical['keys'] == [dict(column=0, direction=1)], 'Raw index metadata')
    nodes, entries = common.tree(data, physical['root'], table['root'], [(4, False)], exact=True)
    require(len(nodes) == 1 and not nodes[0]['children'] and (role == 'control' or nodes[0]['prefix'] == 0), 'Single root leaf')
    require(common.map_pages(data, physical['map']) == {physical['root']}, 'Isolated index map')
    expected_entries = sorted(common.long_key(r['values'][0]) + common.locator_bytes(r['page'], r['row']) for r in rows)
    require(entries == expected_entries and len(entries) == len(set(ids)) == table['row_count'] == physical['entry_count'],
            'Key/locator/count bijection')
    pages = table['data_pages']
    require(common.map_pages(data, table['maps']['available']) <= set(pages), 'Available ownership')
    return dict(count=len(entries), nodes=nodes, data_pages=pages)


def prepare(images: Path, revision: str, spec: dict, stdout: str) -> None:
    arms = spec['arms']
    for arm in arms:
        patch_check((images / f"{arm['name']}-original.mdb").read_bytes(), (images / f"{arm['name']}-candidate.mdb").read_bytes(), arm)
    files = {p.name: identity(p) for p in sorted(images.glob('*.mdb'))}
    common.write(images / MANIFEST, dict(document_type='indexed_boundary_inputs', source_revision=revision, arms=arms, files=files))


def evaluate(images: Path, outbox: Path) -> dict:
    manifest_path = images / MANIFEST
    manifest = common.read(manifest_path)
    report = dict(status='failed', source_revision=manifest['source_revision'], manifest=identity(manifest_path), observations=[], error=None)
    try:
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, manifest_path, 'dao_indexed_boundary_result', manifest['source_revision'])
        require(result['mutation_started'] is True, 'Native mutation started')
        arms = manifest['arms']
        require(set(result['captures']) == {f"{a['name']}-{r}.mdb" for a in arms for r in ROLES}, 'Complete captures')
        require(set(result['operations']) == {a['name'] for a in arms} == {'space', 'eof', 'duplicate'}, 'Operations inventory')
        for name in ('space', 'eof'):
            require(result['operations'][name] == dict(status='inserted'), 'Native insertion')
        duplicate = result['operations']['duplicate']
        require(duplicate['status'] == 'duplicate' and 3022 in duplicate['numbers'], 'Native duplicate rejection')
        for arm in arms:
            snapshots, raw = {}, {}
            for role in ROLES:
                name = f"{arm['name']}-{role}.mdb"
                capture = result['captures'][name]
                path = outbox / name
                require(capture['before'] == capture['after'] == identity(path), 'Read-only identity')
                if role != 'control':
                    require(identity(path) == manifest['files'][name], 'Rust image identity')
                snapshots[role] = expected(capture['snapshot'], arm, role)
                table, rows = definition(path.read_bytes())
                require(table['row_count'] == table['physical_indexes'][0]['entry_count'] == len(rows), 'Fresh insertion count correlation')
                raw[role] = raw_check(path.read_bytes(), arm, role)
            require(snapshots['candidate'] == snapshots['control'], 'Full native control comparison')
            metadata = []
            for snapshot in snapshots.values():
                value = copy.deepcopy(snapshot)
                value.pop('traversal')
                value.pop('seek')
                value['user_tables'][0].pop('rows')
                metadata.append(value)
            require(all(m == metadata[0] for m in metadata), 'Unrelated metadata/Notes preservation')
            patch = patch_check((outbox / f"{arm['name']}-original.mdb").read_bytes(), (outbox / f"{arm['name']}-candidate.mdb").read_bytes(), arm)
            report['observations'].append(dict(arm=arm['name'], patch=patch, raw=raw))
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    return report
