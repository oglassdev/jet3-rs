"""Allocation lifecycles (EXP-0254/0256): tables whose rows or 1800-byte Memo/OLE payloads
push the file past the 1024-page inline map and the 16352-page bitmap slot. Every map,
row, payload and index is decoded; DAO controls are compared through canonical row digests;
three damaged-map edits must be refused with the whole image preserved; a continuation round
lets Rust edit native DAO outputs (and widen an inline map to an indirect one).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

import structure
from registry import common
from registry.common import identity, require

SCRIPT = common.REGISTRY / 'allocation_lifecycle.ps1'
EXTRA = [common.REGISTRY / 'allocation_lifecycle.cs']
MANIFEST = 'allocation-lifecycle.json'
DOCUMENT = 'dao_allocation_lifecycle_mutation_result'
GENERATOR = common.ROOT / 'target/debug/examples/allocation_candidate'
CONFIG = {'rows-inline': (1000, False), 'rows-slot': (16350, False),
          'payload-inline': (512, True), 'payload-slot': (8200, True)}
NOTES_FIELDS = [['Id', 4, 4, False], ['Body', 10, 64, False]]
TABLES = ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes']


# --- Recipe and expected rows --------------------------------------------------------

def operations(base, old, deleted):
    return [dict(kind='insert', id=base, seed=base), dict(kind='replace', old=old, id=base + 1, seed=base + 1), dict(kind='delete', id=deleted)]


def recipe(name):
    count, payload = CONFIG[name]
    if payload:
        fields = [['Id', 4, 4, False], ['Tag', 4, 4, False], ['Body', 12, 0, False], ['Blob', 11, 0, False]]
    else:
        fields = [['Id', 4, 4, False]] + [[f'Pad{c}', 10, 255, True] for c in range(4)]
    ids = (0, 2, 3, 4, 5, 6, count - 1, 100000, 100001, 200000, 200001, 300000, 300001, 400000, 400063, 400064, 999999)
    indexes = [dict(name='ById', fields=[[0, False]], primary=True, unique=True, required=True, queries=[[id] for id in ids])]
    if payload:
        indexes.append(dict(name='ByTag', fields=[[1, True]], primary=False, unique=False, required=False, queries=[[n] for n in range(-9, 10)]))
    return dict(name=name, initial_count=count, payload=payload, fields=fields, indexes=indexes,
                stages=[dict(name='original', operations=[]), dict(name='mutated', operations=operations(100000, 3, 2))],
                native=operations(200000, 4, 5))


def row_values(case, id, seed):
    """Stored bytes per column, as allocation_lifecycle.cs writes them."""
    result = [struct.pack('<i', id)]
    if case['payload']:
        result.extend([None if seed % 13 == 0 else struct.pack('<i', seed % 17 - 8),
                       None if seed == 0 else bytes([65 + seed % 26]) * 1800,
                       None if seed == 1 else bytes((n * 37 + seed) % 256 for n in range(1800))])
    else:
        result.extend(bytes([65 + (seed + 3 * c) % 26]) * 255 for c in range(4))
    return result


def apply(model, edits):
    for op in edits:
        if op['kind'] == 'delete':
            require(op['id'] in model, 'Model deletion exists')
            del model[op['id']]
            continue
        if op['kind'] == 'replace':
            require(op['old'] in model, 'Model replacement exists')
            del model[op['old']]
        require(op['id'] not in model, 'Model insert is unique')
        model[op['id']] = op['seed']


def expected(case, model):
    return {id: row_values(case, id, seed) for id, seed in sorted(model.items())}


def stages(case):
    model = dict(enumerate(range(case['initial_count'])))
    for stage in case['stages']:
        apply(model, stage['operations'])
        yield stage, model.copy()


def feed(state, value):
    state.update(struct.pack('<i', -1 if value is None else len(value)))
    if value is not None:
        state.update(value)


def canonical_digest(rows):
    state = hashlib.sha256()
    for row in rows:
        for cell in row:
            feed(state, cell)
    return state.hexdigest()


def key(values, definition):
    result = b''
    for ordinal, descending in definition['fields']:
        value = values[ordinal]
        component = b'\0' if value is None else b'\x7f' + ((int.from_bytes(value, 'little', signed=True) & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')
        result += bytes(b ^ 255 for b in component) if descending else component
    return result


# --- Raw image checks ----------------------------------------------------------------

def map_locations(table, notes):
    locations = {'global': dict(page=1, row=0), 'table_owned': table['maps']['owned'], 'table_available': table['maps']['available'],
                 'notes_owned': notes['maps']['owned'], 'notes_available': notes['maps']['available']}
    for physical in table['physical_indexes']:
        locations['index' + str(physical['index'])] = physical['map']
    for group in table['long_value_maps']:
        for role in ('owned', 'available'):
            locations[f"lval{group['column']}_{role}"] = group[role]
    return locations


def check_maps(table, notes, locations, data):
    """Every map record with disjoint ownership, availability subsets and separate metadata."""
    require(len({(v['page'], v['row']) for v in locations.values()}) == len(locations), 'Distinct map rows')
    maps, members, bitmaps = {}, {}, set()
    for role, where in locations.items():
        maps[role], members[role] = structure.map_record(data, where, role)
        references = {p for p in maps[role]['references'] if p}
        require(not references & bitmaps, 'Independent role bitmap pages')
        bitmaps |= references
    all_owned = set()
    for role, owned in members.items():
        if not (role.endswith('_owned') or role.startswith('index')):
            continue
        require(not owned & all_owned and not owned & members['global'], 'Disjoint globally allocated ownership: ' + role)
        all_owned |= owned
        if role.endswith('_owned'):
            require(members[role.replace('_owned', '_available')] <= owned, 'Availability subset')
    metadata = set(table['pages']) | set(notes['pages']) | {p['page'] for p in locations.values()} | bitmaps
    require(not metadata & (members['global'] | all_owned), 'Independent allocated metadata')
    return maps, members


def raw_rows(data, case, table, members, expected_rows):
    """Direct rows with complete 1800-byte external payloads and exact LVAL slot reachability."""
    require(not table['long_value_pages'], 'Table owns data, not LVAL pages')
    rows = common.table_rows(data, table, wide=True)
    require(table['row_count'] == len(rows) == len(expected_rows), 'Complete live row count')
    decoded, locators = {}, []
    for row in rows:
        id = row['values'][0]
        values = []
        for ordinal, value in enumerate(row['values']):
            kind = case['fields'][ordinal][1]
            if value is None:
                values.append(None)
            elif kind == 4:
                values.append(struct.pack('<i', value))
            elif kind == 10:
                values.append(value.encode('cp1252'))
            else:
                word = int.from_bytes(bytes.fromhex(row['descriptors'][ordinal])[:4], 'little')
                require(word & 0xffffff == 1800 and word & 0xff000000 in (0, 0x40000000), 'Finite external payload kind/length')
                values.append(bytes.fromhex(value))
        require(id not in decoded and values == expected_rows.get(id), 'Complete raw row/payload bytes: ' + str(id))
        decoded[id] = values
        locators.append([id, row['page'], row['row']])
    require(set(decoded) == set(expected_rows), 'Complete raw Id inventory')
    for group in table['long_value_maps']:
        for page in members[f"lval{group['column']}_owned"]:
            for entry in structure.directory(common.page_bytes(data, page), page):
                require(not entry['hidden'] or (entry['overflow'] and entry['start'] == entry['end']), 'Empty LVAL tombstone')
    return decoded, locators


def check_indexes(data, case, table, members, decoded, locators, rust):
    logical = {i['name']: i['physical_index'] for i in table['logical_indexes']}
    require(set(logical) == {i['name'] for i in case['indexes']} and len(table['physical_indexes']) == len(logical), 'Complete raw index inventory')
    if rust is not None:
        names = [i['name'] for i in rust]
        require(len(names) == len(set(names)) and set(names) == set(logical), 'Exact unique Rust index inventory')
        rust = {i['name']: i for i in rust}
    layout = {}
    for definition in case['indexes']:
        record = next(i for i in table['logical_indexes'] if i['name'] == definition['name'])
        require(record['class'] == int(definition['primary']), 'Raw logical index class')
        physical = table['physical_indexes'][logical[definition['name']]]
        require(physical['keys'] == [dict(column=c, direction=int(not d)) for c, d in definition['fields']]
                and physical['flags'] == int(definition['unique']) + 8 * int(definition['required']), 'Raw index metadata')
        nodes, entries = structure.tree(data, physical['root'], table['root'], [(4, d) for _, d in definition['fields']])
        wanted = sorted(key(decoded[id], definition) + common.locator_bytes(page, slot) for id, page, slot in locators)
        require(entries == wanted, 'Every physical key/locator: ' + definition['name'])
        owned = members['index' + str(physical['index'])]
        visited = {n['page'] for n in nodes}
        require(visited <= owned, 'All index nodes owned')
        for page in owned:
            image = common.page_bytes(data, page)
            require(image[0] in (3, 4) and int.from_bytes(image[4:8], 'little') == table['root'], 'Owned index kind/owner')
        depth = max(n['depth'] for n in nodes)
        layout[definition['name']] = dict(nodes=sorted(visited), reserved=sorted(owned - visited), depth=depth)
        if rust is not None:
            actual = rust[definition['name']]
            require(actual['metadata'] == dict(logical=record['raw_hex'], prefix=physical['prefix_hex'] + physical['entry_count'].to_bytes(4, 'little').hex(),
                                               root=physical['root'], flags=physical['flags'], map=[physical['map']['page'], physical['map']['row']],
                                               keys=[[k['column'], k['direction']] for k in physical['keys']]), 'Complete Rust index definition receipt')
            require(actual['entries'] == [[e[:-4].hex(), int.from_bytes(e[-4:-1], 'big'), e[-1]] for e in entries]
                    and sorted(actual['nodes']) == sorted(visited) and actual['depth'] == depth, 'Complete Rust/raw index receipt')
    return layout


def inspect(data, case, expected_rows, receipt=None, receipt_root=None):
    definitions = common.tables(data)
    table, notes = definitions['Items'], definitions['Notes']
    require([[c['name'], c['type'], c['size']] for c in table['columns']] ==
            [[name, structure.PHYSICAL_TYPES[kind], size] for name, kind, size, _ in case['fields']], 'Complete raw schema')
    locations = map_locations(table, notes)
    maps, members = check_maps(table, notes, locations, data)
    decoded, locators = raw_rows(data, case, table, members, expected_rows)
    digest = canonical_digest(decoded[id] for id in sorted(decoded))
    index_layout = check_indexes(data, case, table, members, decoded, locators, receipt['tables']['Items']['indexes'] if receipt else None)
    require(not notes['long_value_pages'] and [r['values'] for r in common.table_rows(data, notes)] == [[7, 'allocation-control']], 'Complete raw Notes')
    note_pages = set(notes['data_pages']) | set(notes['pages']) | {p['page'] for p in notes['maps'].values()}
    note_pages |= {p for role in ('notes_owned', 'notes_available') for p in maps[role]['references'] if p}
    notes_identity = {str(p): common.sha(common.page_bytes(data, p)) for p in sorted(note_pages)}
    if receipt:
        require(receipt['pages'] * common.PAGE == len(data) and set(receipt['tables']) == {'Items', 'Notes'}, 'Complete Rust geometry/inventory')
        for name, definition in definitions.items():
            actual = receipt['tables'][name]
            require(actual['fields'] == [[c['name'], c['type_code'], c['size'], c['class']] for c in definition['columns']], 'Rust/raw complete column receipt')
            require(actual['count'] == actual['declared_count'] == (len(expected_rows) if name == 'Items' else 1), 'Rust row count')
            wanted = digest if name == 'Items' else canonical_digest([[struct.pack('<i', 7), b'allocation-control']])
            path = receipt_root / actual['stream']
            require(path.name == actual['stream'] and common.sha(path.read_bytes()) == wanted, 'Complete Rust canonical row stream')
        require(receipt['tables']['Items']['locators'] == sorted(locators) and receipt['tables']['Notes']['indexes'] == [], 'Rust locators and Notes indexes')
    return dict(pages=len(data) // common.PAGE, maps=maps, indexes=index_layout, digest=digest, count=len(expected_rows),
                locators=sorted(locators), notes=notes_identity)


def raw_check(path, case, model, receipt_path=None):
    receipt = common.read(receipt_path) if receipt_path else None
    return inspect(path.read_bytes(), case, expected(case, model), receipt, receipt_path.parent if receipt_path else None)


def boundary(layout, case):
    minimum = 16352 if case['name'].endswith('slot') else 1024
    require(layout['pages'] > minimum, 'Actual file crosses allocation boundary: ' + case['name'])
    if minimum == 16352:
        global_map = layout['maps']['global']
        require(global_map['kind'] == 1 and len(global_map['references']) >= 2 and global_map['references'][1] != 0, 'Global second bitmap slot is active')
        for role in (['lval2_owned', 'lval3_owned'] if case['payload'] else ['table_owned']):
            require(any(end > minimum for _, end in layout['maps'][role]['members']), 'Owned pages cross bitmap slot: ' + role)


# --- Generator runs and refusals -----------------------------------------------------

def run(generator, args, root, label):
    done = subprocess.run([str(generator), *map(str, args)], cwd=common.ROOT, capture_output=True, text=True, timeout=900)
    (root / (label + '.stdout.log')).write_text(done.stdout)
    (root / (label + '.stderr.log')).write_text(done.stderr)
    require(done.returncode == 0, f'{label} failed ({done.returncode}); see retained logs in {root}')
    return done.stdout


def rollback(directory, layouts, generator):
    """Three damaged indirect-map references; Rust must refuse each and preserve the image."""
    definitions = [('outside-eof', 'rows-slot', 'global'), ('data-as-bitmap', 'payload-slot', 'lval2_owned'),
                   ('bitmap-alias', 'payload-slot', 'lval2_owned')]
    receipts = []
    for name, case_name, role in definitions:
        stem = 'refusal-' + name
        source = directory / (case_name + '-mutated.mdb')
        image = bytearray(source.read_bytes())
        layout = layouts[case_name]
        mapping = layout['maps'][role]
        require(mapping['kind'] == 1 and mapping['references'][0], 'Rollback exercises an active indirect map')
        if name == 'outside-eof':
            replacement = len(image) // common.PAGE + 1
        elif name == 'data-as-bitmap':
            replacement = layout['locators'][0][1]
        else:
            replacement = layout['maps']['lval3_owned']['references'][0]
        require(replacement != mapping['references'][0] and replacement != 0, 'Rollback changes active reference')
        where = mapping['locator']
        entry = structure.directory(common.page_bytes(image, where['page']), where['page'])[where['row']]
        offset = where['page'] * common.PAGE + entry['start'] + 1
        image[offset:offset + 4] = replacement.to_bytes(4, 'little')
        before, after = directory / (stem + '-before.mdb'), directory / (stem + '-after.mdb')
        before.write_bytes(image)
        result = json.loads(run(generator, ['refuse', before, after, case_name], directory, stem))
        require(result['preserved'] and before.read_bytes() == after.read_bytes(), 'Whole damaged image preserved')
        receipts.append(dict(name=name, source=identity(source), before=identity(before), after=identity(after),
                             offset=offset, old_reference=mapping['references'][0], replacement=replacement, result=result))
    common.write(directory / 'refusals.json', receipts)
    return receipts


def verify_refusals(directory, manifest):
    require(common.read(directory / 'refusals.json') == manifest['refusals']
            and [r['name'] for r in manifest['refusals']] == ['outside-eof', 'data-as-bitmap', 'bitmap-alias'], 'Rollback receipt inventory')
    for receipt in manifest['refusals']:
        stem = 'refusal-' + receipt['name']
        require(identity(directory / (stem + '-before.mdb')) == receipt['before'] == receipt['after'] == identity(directory / (stem + '-after.mdb'))
                and receipt['result']['preserved'], 'Retained whole-image rollback')


def manifest_files(directory):
    return {p.name: identity(p) for p in sorted(directory.iterdir())
            if p.is_file() and p.suffix in ('.mdb', '.json', '.bin', '.log') and p.name != MANIFEST}


def prepare(images: Path, revision: str, spec: dict, stdout: str) -> None:
    cases = [recipe(name) for name in CONFIG]
    layouts = {}
    for case in cases:
        notes = None
        for stage, model in stages(case):
            stem = case['name'] + '-' + stage['name']
            layout = raw_check(images / (stem + '.mdb'), case, model, images / (stem + '.snapshot.json'))
            boundary(layout, case)
            if notes is None:
                notes = layout['notes']
            require(layout['notes'] == notes, 'Unrelated Notes pages preserved: ' + stem)
            layouts[case['name']] = layout
        case['notes_pages'] = notes
    refusals = rollback(images, layouts, GENERATOR)
    common.write(images / MANIFEST, dict(document_type='allocation_lifecycle_inputs', round='mutations', source_revision=revision,
                                         cases=cases, files=manifest_files(images), refusals=refusals))


def prepare_continue(images: Path, outbox: Path, output: Path, generator: Path, revision: str) -> None:
    first = common.read(images / MANIFEST)
    result = common.read(outbox / 'result.json')
    require(result['manifest_sha256'] == identity(images / MANIFEST)['sha256'] and result['source_revision'] == first['source_revision']
            and result['round'] == 'mutations' and result['error'] is None, 'Continuation parent binding')
    output.mkdir(parents=True)
    cases, receipts = [], []
    try:
        for case, observed in zip(first['cases'], result['cases']):
            case = copy.deepcopy(case)
            require(case['name'] == observed['name'] and observed['status'] == 'pass', 'Parent case')
            _, model = list(stages(case))[-1]
            apply(model, case['native'])
            capture = observed['native']['control']['capture']
            source = outbox / capture['file']
            require(identity(source) == capture['before'] == capture['after'], 'Closed native continuation input')
            normalized(capture, case, expected(case, model))
            layout = raw_check(source, case, model)
            boundary(layout, case)
            edits = operations(300000, 6, 200000)
            if case['name'] == 'rows-inline':
                edits += [dict(kind='insert', id=id, seed=id) for id in range(400000, 400064)]
            apply(model, edits)
            child = output / case['name']
            run(generator, ['continue', source, child, case['name']], output, case['name'])
            source_name = case['name'] + '-continuation-source.mdb'
            shutil.copy2(source, output / source_name)
            for path in child.iterdir():
                shutil.copy2(path, output / path.name)
            stem = case['name'] + '-continued'
            changed = raw_check(output / (stem + '.mdb'), case, model, output / (stem + '.snapshot.json'))
            require(changed['notes'] == layout['notes'], 'Native-input continuation preserves Notes pages')
            if case['name'] == 'rows-inline':
                for role in ('global', 'table_owned'):
                    require(layout['maps'][role]['kind'] == 0 and layout['maps'][role]['length'] > 133
                            and changed['maps'][role]['kind'] == 1 and changed['maps'][role]['length'] == 133,
                            'Native widened inline map converts to compact indirect row: ' + role)
            case.update(source_file=source_name, candidate_file=stem + '.mdb', expected_model=sorted(model.items()),
                        operations=edits, notes_pages=layout['notes'])
            cases.append(case)
            receipts.append(dict(name=case['name'], source=identity(source), output=identity(output / (stem + '.mdb'))))
        common.write(output / MANIFEST, dict(document_type='allocation_lifecycle_inputs', round='continuation', source_revision=revision,
                                             cases=cases, files=manifest_files(output), parent_manifest=identity(images / MANIFEST),
                                             parent_result=identity(outbox / 'result.json')))
    finally:
        common.write(output / 'continuation-preparation.json', receipts)


# --- DAO comparison ------------------------------------------------------------------

def normalized(capture, case, rows):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == TABLES and value['queries'] == value['relations'] == [], 'Complete DAO database inventory')
    require([t['name'] for t in value['schema']] == ['Items', 'Notes'], 'Complete DAO user schema inventory')
    items, notes = value['schema']
    for table, fields in ((items, case['fields']), (notes, NOTES_FIELDS)):
        require(table['attributes'] == 0 and [[f['name'], f['type'], f['size']] for f in table['fields']] == [f[:3] for f in fields],
                'Complete DAO field names/types/sizes')
        for field, spec in zip(table['fields'], fields):
            attributes = 1 if spec[1] == 4 or spec[3] else 2
            require(field['attributes'] == attributes and field['required'] is False and field['allow_zero_length'] is False
                    and field['default_value'] == '', 'Complete DAO field properties: ' + field['name'])
    indexes = [dict(name=i['name'], primary=i['primary'], unique=i['unique'], required=i['required'], ignore_nulls=False, foreign=False,
                    fields=[dict(name=case['fields'][c][0], attributes=int(d)) for c, d in i['fields']]) for i in case['indexes']]
    require(sorted(items['indexes'], key=lambda i: i['name']) == sorted(indexes, key=lambda i: i['name']) and notes['indexes'] == [], 'Complete DAO indexes')
    require(value['count'] == len(rows) and value['digest'] == canonical_digest(rows[id] for id in sorted(rows))
            and value['notes'] == [[7, 'allocation-control']], 'Complete DAO row/payload digest and unrelated Notes')
    require(set(value['index_reads']) == {i['name'] for i in case['indexes']}, 'Complete DAO traversal inventory')
    for index in case['indexes']:
        actual = value['index_reads'][index['name']]
        ids = actual['ids']
        keys = {id: key(row, index) for id, row in rows.items()}
        require(sorted(ids) == sorted(rows) and [keys[id] for id in ids] == sorted(keys.values()), 'Every directed DAO index entry')
        require([s['query'] for s in actual['seek']] == index['queries'], 'Complete full-key Seek inventory')
        for seek in actual['seek']:
            query_row = [None] * len(case['fields'])
            for (column, _), query in zip(index['fields'], seek['query']):
                query_row[column] = struct.pack('<i', query)
            wanted = key(query_row, index)
            matches = sorted(id for id, k in keys.items() if k == wanted)
            if matches:
                require(seek['id'] in matches and seek['digest'] == canonical_digest([rows[seek['id']]]), 'Seek returns complete matching row/payload')
            else:
                require(seek['id'] is None and seek['digest'] is None, 'Seek returns absence')
            seek['matches'] = matches
            del seek['id'], seek['digest']
        actual['ids'] = sorted(ids, key=lambda id: (keys[id], id))
    items['indexes'].sort(key=lambda i: i['name'])
    return value


def retained_capture(outbox, capture, case, model, notes, receipt_path=None):
    path = outbox / capture['file']
    require(path.name == capture['file'], 'Capture filename')
    image = identity(path)
    require(capture['before'] == capture['after'] == image, 'Read-only closed capture image identity')
    snapshot = normalized(capture, case, expected(case, model))
    if receipt_path is None:
        receipt_path = outbox / (path.stem + '.reader.json')
        run(GENERATOR, ['inspect', path, receipt_path], outbox, path.stem + '-reader')
    layout = raw_check(path, case, model, receipt_path)
    require(layout['notes'] == notes, 'Every unrelated Notes page preserved')
    boundary(layout, case)
    return snapshot, dict(image=image, layout=layout, reader=identity(receipt_path))


def aggregate(outbox: Path) -> dict:
    return common.aggregate(outbox, list(CONFIG), DOCUMENT)


def compare_continuation(images, outbox, manifest, case, observed, outcome):
    model = {int(id): seed for id, seed in case['expected_model']}
    require(observed['operation']['count'] == len(case['operations'])
            and observed['operation']['before'] == manifest['files'][case['source_file']], 'Continuation source binding')
    pairs, details = {}, {}
    for role in ('candidate', 'control'):
        receipt = images / (case['name'] + '-continued.snapshot.json') if role == 'candidate' else None
        pairs[role], details[role] = retained_capture(outbox, observed['roles'][role], case, model, case['notes_pages'], receipt)
    require(details['candidate']['image'] == manifest['files'][case['candidate_file']]
            and details['control']['image'] == observed['operation']['after'], 'Continuation output binding')
    require(pairs['candidate'] == pairs['control'], 'Full native-input continuation comparison')
    outcome['checkpoints'].append(dict(name='continued', roles=details))


def compare_mutations(images, outbox, manifest, case, observed, outcome):
    require([s['name'] for s in observed['stages']] == [s['name'] for s in case['stages']], 'Native checkpoint inventory')
    created = outbox / observed['created']['file']
    chain = identity(created)
    require(chain == observed['created']['image'], 'Native creation image')
    native_notes = raw_check(created, case, dict(enumerate(range(case['initial_count']))))['notes']
    details = {}
    for (stage, model), checkpoint in zip(stages(case), observed['stages']):
        require(checkpoint['operations'] == stage['operations'] and checkpoint['mutation']['before'] == chain
                and checkpoint['mutation']['count'] == len(stage['operations']), 'Native operation chain')
        pairs, details = {}, {}
        stem = case['name'] + '-' + stage['name']
        for role in ('candidate', 'control'):
            receipt = images / (stem + '.snapshot.json') if role == 'candidate' else None
            notes = case['notes_pages'] if role == 'candidate' else native_notes
            pairs[role], details[role] = retained_capture(outbox, checkpoint['roles'][role], case, model, notes, receipt)
        require(details['candidate']['image'] == manifest['files'][stem + '.mdb']
                and details['control']['image'] == checkpoint['mutation']['after'], 'Checkpoint output binding')
        require(pairs['candidate'] == pairs['control'], 'Complete paired DAO schema/rows/traversal/Seek')
        chain = details['control']['image']
        outcome['checkpoints'].append(dict(name=stage['name'], roles=details))
    apply(model, case['native'])
    pairs, native_details = {}, {}
    for role in ('candidate', 'control'):
        native = observed['native'][role]
        require(native['mutation']['before'] == details[role]['image'] and native['mutation']['count'] == len(case['native']), 'Native successor source binding')
        notes = case['notes_pages'] if role == 'candidate' else native_notes
        pairs[role], native_details[role] = retained_capture(outbox, native['capture'], case, model, notes)
        require(native['mutation']['after'] == native_details[role]['image'], 'Native successor output binding')
    require(pairs['candidate'] == pairs['control'], 'Complete native successor pair')
    outcome['native'] = native_details


def evaluate(images: Path, outbox: Path) -> dict:
    manifest_path = images / MANIFEST
    manifest = common.read(manifest_path)
    report = dict(status='failed', round=manifest['round'], source_revision=manifest['source_revision'], manifest=identity(manifest_path), cases=[], error=None)
    try:
        require(identity(outbox / MANIFEST) == report['manifest'], 'Retained manifest identity')
        for name, pin in manifest['files'].items():
            require(identity(images / name) == identity(outbox / name) == pin, 'Retained input: ' + name)
        result = common.read(outbox / 'result.json')
        report['result'] = identity(outbox / 'result.json')
        common.check_result(result, manifest_path, DOCUMENT, manifest['source_revision'])
        require(result['round'] == manifest['round'], 'Native round')
        report['environment'] = result['environment']
        require([c['name'] for c in result['cases']] == list(CONFIG) == [c['name'] for c in manifest['cases']], 'Case inventory')
        compare = compare_continuation if manifest['round'] == 'continuation' else compare_mutations
        for case, observed in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None)
            report['cases'].append(outcome)
            try:
                require(observed['status'] == 'pass' and observed['error'] is None, 'Native case completed')
                compare(images, outbox, manifest, case, observed, outcome)
                outcome['status'] = 'accepted'
            except Exception as error:
                outcome['error'] = f'{type(error).__name__}: {error}'
        if manifest['round'] == 'mutations':
            verify_refusals(images, manifest)
            report['refusals'] = manifest['refusals']
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more allocation cases failed')
        report['status'] = 'accepted'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    return report
