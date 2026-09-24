"""Indexed row insertion and deletion (EXP-0215/0216/0219/0221).

Each arm changes one Items(Id, Value) table with a single Long index: ascending and
descending insertion, a 200-key capacity insertion and three deletions. The Rust patch is
reconstructed byte for byte from its receipt; DAO then compares three replicas of the
original, candidate, native control, next-row insertion and duplicate refusal.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import structure
from registry import common
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'indexed_rows.ps1'
MANIFEST = 'indexed-rows.json'
ROLES = ['original', 'candidate', 'control-original', 'control', 'candidate-next', 'control-next', 'candidate-duplicate', 'control-duplicate']
COLUMNS = ['Id', 'Value']


def arms(spec):
    """Suite arms with `rows` counts and `queries_range` expanded."""
    result = []
    for arm in spec['arms']:
        arm = dict(arm, rows=[[i, 100 + i] for i in range(arm['rows'])])
        arm['queries'] = list(range(*arm.pop('queries_range', [0, 0]))) + arm['queries']
        result.append(arm)
    return result


def rows_for(arm, role):
    rows = copy.deepcopy(arm['rows'])
    if role not in ('original', 'control-original'):
        if arm['kind'] == 'insert':
            rows.append(arm['insert'])
        else:
            rows = [r for r in rows if r[0] not in arm['delete']]
    if role.endswith('-next'):
        rows.append(arm['follow'])
    return sorted(rows)


def definition(data):
    table = common.tables(data, ['Items'])['Items']
    require(not table['long_value_pages'] and len(table['physical_indexes']) == len(table['logical_indexes']) == 1, 'One scalar index')
    return table, common.table_rows(data, table)


def observe(data, arm, expected_rows, candidate):
    """Rows, maps and the exact index tree (EXP-0062/0057/0065/0126)."""
    require(len(data) % common.PAGE == 0 and 20 <= len(data) // common.PAGE <= 8192, 'Image page bound')
    table, rows = definition(data)
    require([(c['name'], c['type'], c['size']) for c in table['columns']] == [(n, 'Long', 4) for n in COLUMNS], 'Physical schema mismatch')
    pages = table['data_pages']
    require(sorted(row['values'] for row in rows) == sorted(expected_rows) and table['row_count'] == len(rows), 'Physical row mismatch')
    available = common.map_pages(data, table['maps']['available'])
    require(available <= set(pages), 'Available pages outside owned data pages')
    physical = table['physical_indexes'][0]
    nodes, entries = common.tree(data, physical['root'], table['root'], [(4, arm['descending'])], exact=True)
    mapped = common.map_pages(data, physical['map'])
    graph = {node['page'] for node in nodes}
    require(graph <= mapped and not mapped.intersection(pages), 'Index map membership mismatch')
    require(not candidate or graph == mapped, 'Candidate index map has unused members')
    by_locator = {(row['page'], row['row']): row['values'] for row in rows}
    seen, keys = set(), []
    for entry in entries:
        locator = (int.from_bytes(entry[-4:-1], 'big'), entry[-1])
        require(locator in by_locator and locator not in seen, 'Missing or repeated row locator')
        seen.add(locator)
        key = common.long_key(by_locator[locator][0], arm['descending'])
        require(entry[:-4] == key, 'Index key and row values disagree')
        keys.append(key)
    require(seen == set(by_locator), 'Index coverage mismatch')
    require(arm['kind'] == 'delete' or physical['entry_count'] == len(set(keys)), 'Index distinct count mismatch')
    depth = max(node['depth'] for node in nodes)
    require(not candidate or depth == 1, 'Candidate did not reach planned depth')
    return dict(name='Items', root=table['root'], row_count=len(rows), data_pages=pages, available_pages=sorted(available),
                indexes=[dict(root=physical['root'], depth=depth, nodes=nodes, mapped_pages=sorted(mapped), leaf_entries=len(entries),
                              distinct_keys=len(set(keys)), stored_counter=physical['entry_count'], physical_flags=physical['flags'],
                              locator_key_sha256=hashlib.sha256(b''.join(entries)).hexdigest())],
                row_locators=[dict(values=r['values'], page=r['page'], slot=r['row']) for r in rows])


def raw_check(data, arm, rows, candidate=False):
    table, _ = definition(data)
    physical = table['physical_indexes'][0]
    require(physical['flags'] == (9 if arm['primary'] else 1) and physical['keys'] == [dict(column=0, direction=int(not arm['descending']))],
            'Physical key metadata')
    result = observe(data, arm, rows, candidate)
    if arm['kind'] == 'delete':
        # EXP-0219: deletion retains the counter; a subsequent insertion adds one.
        require(physical['entry_count'] == len(arm['rows']) + int(arm['follow'] in rows), 'Retained deletion counter')
    return result


def patch_check(before, after, arm, receipt):
    """Replays the receipt's data-page, count and leaf patches on the original."""
    raw_check(before, arm, arm['rows'], True)
    raw_check(after, arm, rows_for(arm, 'candidate'), True)
    expected = bytearray(before)
    actions = receipt['actions']
    require(len(actions) == (1 if arm['kind'] == 'insert' else len(arm['delete'])), 'Action inventory')
    for step, action in enumerate(actions):
        table, rows = definition(bytes(expected))
        require(table['root'] == receipt['root'], 'Receipt root')
        page, slot = action['page'], action['slot']
        offset = page * common.PAGE
        image = bytes(expected[offset:offset + common.PAGE])
        directory = structure.directory(image, page)
        require(image[:2] == b'\x01\x01' and int.from_bytes(image[4:8], 'little') == table['root'] and directory, 'Data page owner/kind')
        lowest = directory[-1]['start']
        free = lowest - 10 - 2 * len(directory)
        require(int.from_bytes(image[2:4], 'little') == free, 'Data free bytes')
        if arm['kind'] == 'insert':
            require(action['kind'] == 'insert' and slot == len(directory) and slot < 256, 'Appended physical slot')
            row = bytes([2]) + b''.join(v.to_bytes(4, 'little', signed=True) for v in arm['insert']) + b'\x03'
            start = lowest - len(row)
            require(start >= 10 + 2 * (slot + 1), 'Row capacity')
            expected[offset + start:offset + lowest] = row
            expected[offset + 10 + slot * 2:offset + 12 + slot * 2] = start.to_bytes(2, 'little')
            expected[offset + 8:offset + 10] = (slot + 1).to_bytes(2, 'little')
            new_free = free - len(row) - 2
            count = len(rows) + 1
        else:
            require(action['kind'] == 'delete' and slot < len(directory), 'Deleted slot')
            selected = [r for r in rows if r['values'][0] == arm['delete'][step]]
            require(len(selected) == 1 and (selected[0]['page'], selected[0]['row']) == (page, slot), 'Deleted Id/locator')
            target = directory[slot]
            start, end = target['start'], target['end']
            length = end - start
            require(length > 0, 'Live deletion')
            expected[offset + lowest + length:offset + end] = image[lowest:start]
            for n in range(slot, len(directory)):
                old = int.from_bytes(image[10 + 2 * n:12 + 2 * n], 'little')
                word = (end | 0xc000) if n == slot else ((old & 0xf000) | ((old & 0x0fff) + length))
                expected[offset + 10 + 2 * n:offset + 12 + 2 * n] = word.to_bytes(2, 'little')
            new_free = free + length
            count = len(rows) - 1
        expected[offset + 2:offset + 4] = new_free.to_bytes(2, 'little')
        root = table['root'] * common.PAGE
        expected[root + 12:root + 16] = count.to_bytes(4, 'little')
        if arm['kind'] == 'insert':
            counter = int.from_bytes(expected[root + 47:root + 51], 'little') + 1
            expected[root + 47:root + 51] = counter.to_bytes(4, 'little')
        _, new_rows = definition(bytes(expected))
        index = table['physical_indexes'][0]['root']
        base = index * common.PAGE
        leaf = bytes(expected[base:base + common.PAGE])
        require(leaf[:2] == b'\x04\x01' and leaf[8:22] == bytes(14), 'Candidate uncompressed root leaf')
        records = sorted(common.long_key(r['values'][0], arm['descending']) + common.locator_bytes(r['page'], r['row']) for r in new_rows)
        require(len(records) == count <= 200, 'Unique leaf capacity')
        bitmap = bytearray(226)
        for n in range(1, count + 1):
            bitmap[n * 9 // 8] |= 1 << (n * 9 % 8)
        expected[base + 2:base + 4] = (1800 - count * 9).to_bytes(2, 'little')
        expected[base + 22:base + 248] = bitmap
        expected[base + 248:base + 248 + count * 9] = b''.join(records)
    require(bytes(expected) == after, 'Exact three-page patches, slack/maps/page0 and unrelated preservation')
    allowed = ('capacity', 'duplicate') if arm['name'] == 'capacity' else ('duplicate',)
    require(receipt['refusal_preserved'] is True and receipt['public_refusal'] in allowed, 'Public duplicate/capacity refusal')
    return dict(actions=actions, refusal=receipt['public_refusal'], changed_offsets=[i for i, (a, b) in enumerate(zip(before, after)) if a != b],
                page0_unchanged=before[:common.PAGE] == after[:common.PAGE])


def expected(snapshot, arm, role):
    value = copy.deepcopy(snapshot)
    value['user_tables'].sort(key=lambda table: table['name'])
    for table in value['user_tables']:
        table['rows'].sort()
    rows = rows_for(arm, role)
    require(value['version'] == '3.0' and value['relations'] == value['queries'] == []
            and value['tables'] == ['Items', *common.SYSTEM_TABLES] and len(value['user_tables']) == 1, 'Complete table inventory')
    table = value['user_tables'][0]
    fields = [dict(name=n, type=4, size=4, attributes=1) for n in COLUMNS]
    index = dict(name='ByKey', primary=arm['primary'], unique=True, required=arm['primary'], foreign=False, ignore_nulls=False,
                 fields=[dict(name='Id', attributes=int(arm['descending']))])
    require(table['name'] == 'Items' and table['attributes'] == 0 and [{k: f[k] for k in ('name', 'type', 'size', 'attributes')} for f in table['fields']] == fields
            and table['indexes'] == [index] and table['rows'] == rows, 'Exact schema/index/full rows')
    require(value['traversal'] == sorted(rows, reverse=arm['descending'])
            and value['seek'] == [dict(query=q, row=next((r for r in rows if r[0] == q), None)) for q in arm['queries']],
            'Full directed traversal/present and missing Seek')
    return value


def prepare(images: Path, revision: str, spec: dict, stdout: str) -> None:
    receipts = json.loads(stdout)
    expanded = arms(spec)
    for arm in expanded:
        patch_check((images / f"{arm['name']}-original.mdb").read_bytes(), (images / f"{arm['name']}-candidate.mdb").read_bytes(),
                    arm, receipts[arm['name']])
    files = {p.name: identity(p) for p in sorted(images.glob('*.mdb'))}
    common.write(images / MANIFEST, dict(document_type='indexed_rows_inputs', source_revision=revision, arms=expanded,
                                         receipts=receipts, files=files))


def evaluate(images: Path, outbox: Path) -> dict:
    manifest_path = images / MANIFEST
    manifest = common.read(manifest_path)
    report = dict(status='failed', source_revision=manifest['source_revision'], manifest=identity(manifest_path), observations=[], error=None)
    try:
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, manifest_path, 'dao_indexed_row_result', manifest['source_revision'])
        require(result['mutation_started'] is True, 'Native mutation started')
        pairs = {(p['arm'], p['replica']): p for p in result['pairs']}
        require(len(pairs) == len(result['pairs']) and set(pairs) == {(a['name'], r) for a in manifest['arms'] for r in range(1, 4)}, 'Complete pairs')
        for arm in manifest['arms']:
            for replica in range(1, 4):
                report['observations'].append(compare_pair(outbox, manifest, arm, replica, pairs[arm['name'], replica]))
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    return report


def compare_pair(outbox, manifest, arm, replica, pair):
    snapshots, images, raw, duplicate_counts = {}, {}, {}, {}
    require(set(pair['captures']) == set(ROLES)
            and set(pair['operations']) == {'control', 'candidate-next', 'control-next', 'candidate-duplicate', 'control-duplicate'},
            'Complete capture/operation inventory')
    for role in ROLES:
        c = pair['captures'][role]
        path = outbox / f"{arm['name']}-r{replica}-{role}.mdb"
        require(c['file'] == path.name and c['status'] == 'pass' and c['error'] is None and c['before'] == c['after'] == identity(path),
                'Unchanged retained image')
        images[role] = identity(path)
        if role in ('original', 'candidate'):
            require(images[role] == manifest['files'][arm['name'] + '-' + role + '.mdb'], 'Rust image identity')
        snapshots[role] = expected(c['snapshot'], arm, role)
        raw[role] = raw_check(path.read_bytes(), arm, rows_for(arm, role), role in ('original', 'candidate'))
        if role.endswith('-duplicate'):
            table, rows = definition(path.read_bytes())
            duplicate_counts[role] = dict(table_count=table['row_count'], distinct_count=table['physical_indexes'][0]['entry_count'], live_rows=len(rows))
    for role in ('control', 'candidate-next', 'control-next'):
        require(pair['operations'][role] == dict(status='complete'), 'Completed mutation')
    for role in ('candidate-duplicate', 'control-duplicate'):
        operation = pair['operations'][role]
        require(operation['accepted'] is False and operation['error'] is not None and 3022 in operation['numbers'], 'Duplicate native rejection')
    require(pair['operations']['candidate-duplicate']['numbers'] == pair['operations']['control-duplicate']['numbers'], 'Matched native duplicate errors')
    for a, b in [('original', 'control-original'), ('candidate', 'control'), ('candidate-next', 'control-next'),
                 ('candidate-duplicate', 'control-duplicate'), ('candidate', 'candidate-duplicate')]:
        require(snapshots[a] == snapshots[b], 'Full control or rejected post-state semantics')
    metadata = []
    for snapshot in snapshots.values():
        value = copy.deepcopy(snapshot)
        value.pop('traversal')
        value.pop('seek')
        value['user_tables'][0].pop('rows')
        metadata.append(value)
    require(all(v == metadata[0] for v in metadata), 'Unrelated metadata changed')
    patch = patch_check((outbox / f"{arm['name']}-r{replica}-original.mdb").read_bytes(), (outbox / f"{arm['name']}-r{replica}-candidate.mdb").read_bytes(),
                        arm, manifest['receipts'][arm['name']])
    return dict(arm=arm['name'], replica=replica, identities=images, raw=raw, patch=patch, duplicate_counts=duplicate_counts, operations=pair['operations'])
