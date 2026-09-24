"""Candidate/native comparisons shared by the suites.

Each check raises Mismatch with a short reason, or returns a JSON record of what it
verified and which recorded differences it allowed. Recorded differences come only from
the suite spec: `dates`, `placement_roles`, `native_residue`, `getter_residue`.
"""

from __future__ import annotations

import copy
import json
import subprocess

import structure

PAGE = structure.PAGE


class Mismatch(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise Mismatch(message)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def differences(left, right, path=''):
    """Paths where two JSON values differ; lists compare positionally."""
    if type(left) is not type(right):
        return [(path, left, right)]
    if isinstance(left, dict):
        if set(left) != set(right):
            return [(path + '/keys', sorted(left), sorted(right))]
        return sum((differences(left[k], right[k], f'{path}/{k}') for k in left), [])
    if isinstance(left, list):
        if len(left) != len(right):
            return [(path + '/length', len(left), len(right))]
        return sum((differences(a, b, f'{path}/{i}') for i, (a, b) in enumerate(zip(left, right))), [])
    return [] if left == right else [(path, left, right)]


def brief(found, limit=3):
    return [(p, str(a)[:100], str(b)[:100]) for p, a, b in found[:limit]]


# --- Locale names --------------------------------------------------------------------

def host(raw: bytes) -> str:
    """Text as DAO 3.6 exposes it: database bytes read through the host ANSI page 1252."""
    return ''.join(chr(b) if b in (0x81, 0x8d, 0x8f, 0x90, 0x9d) else bytes([b]).decode('cp1252') for b in raw)


def host_names(value, code_page: int):
    """Every string of a request as DAO and the raw decoder see it in a `code_page` database."""
    if isinstance(value, str):
        return host(value.encode(f'cp{code_page}'))
    if isinstance(value, list):
        return [host_names(v, code_page) for v in value]
    if isinstance(value, dict):
        return {k: host_names(v, code_page) for k, v in value.items()}
    return value


def host_case(case: dict) -> dict:
    """A case with `code_page` restated in host names for DAO and raw comparisons."""
    if 'code_page' not in case:
        return case
    page = case['code_page']
    result = copy.deepcopy(case)
    for step in result['steps']:
        step['request'] = host_names(step['request'], page)
    for key in ('dates', 'affected_tables'):
        if key in result:
            result[key] = host_names(result[key], page)
    roles = result.get('placement_roles')
    if isinstance(roles, dict):
        result['placement_roles'] = {host_names(k, page): v for k, v in roles.items()}
    elif roles:
        result['placement_roles'] = host_names(roles, page)
    return result


def refused(case) -> bool:
    return any(step.get('expected_returncode', 0) for step in case['steps'])


# --- DAO readbacks -------------------------------------------------------------------

def readback_view(item, dates, unordered_rows=False) -> dict:
    """Observation without file identity; only the named objects' dates are masked.

    `unordered_rows` sorts table-scan rows, whose order follows physical page placement;
    index traversals keep their order.
    """
    view = copy.deepcopy({k: v for k, v in item.items() if k not in ('file', 'identity')})
    for prop in view['database']['properties']:
        if prop['name'] == 'Name':  # the opened copy's absolute path
            prop['value'] = '<opened path>'
    for table in view['tables']:
        if unordered_rows:
            table['rows'] = sorted(table['rows'], key=canonical)
        if table['name'] in dates:
            for prop in table['properties']:
                if prop['name'] in ('DateCreated', 'LastUpdated'):
                    prop['value'] = '<normalized date>'
    return view


def getter(view, table_name, index_name, name):
    table, = [t for t in view['tables'] if t['name'] == table_name]
    index, = [i for i in table['indexes'] if i['name'] == index_name]
    prop, = [p for p in index['properties'] if p['name'] == name]
    return prop


def readback(case, candidate, native, images) -> dict:
    """Complete DAO readback equality of the candidate and native images."""
    for item, data in ((candidate, images['candidate']), (native, images['native'])):
        require(item['identity'] == {'size': len(data), 'sha256': structure.sha(data)}, 'readback identity: ' + item['file'])
    dates = set(case.get('dates', []))
    unordered = case.get('unordered_rows', False)
    left, right = readback_view(candidate, dates, unordered), readback_view(native, dates, unordered)
    masked = []
    for residue in case.get('getter_residue', []):
        a = getter(left, residue['table'], residue['index'], residue['property'])
        b = getter(right, residue['table'], residue['index'], residue['property'])
        require([a['value'], b['value']] == [residue['candidate'], residue['native']], 'recorded DAO getter residue')
        b['value'] = a['value']
        masked.append(residue)
    found = differences(left, right)
    require(not found, 'DAO readback difference: ' + str(brief(found)))
    if refused(case):
        require(images['candidate'] == images['input'], 'Rust refusal changed its input')
    return {'dates': sorted(dates), 'getter_residue': masked}


# --- Native outcomes -----------------------------------------------------------------

def outcome(case, native, images) -> dict:
    """Native step outcomes match the case kind; Rust refusals leave the input exact."""
    kind = case.get('kind', 'accepted')
    if native is None:
        require(kind == 'rust-only', 'missing native outcome')
        steps = []
    else:
        steps = native['steps']
        failed = [s for s in steps if not s['ok']]
        if kind in ('refused', 'residue'):
            require(len(failed) == 1 and case['dao_error'] in failed[0]['error']['numbers'],
                    f'expected DAO error {case["dao_error"]}: {[s.get("error") for s in failed]}')
        else:
            require(not failed and len(steps) == case['native_steps'], 'native steps completed')
    record = {'kind': kind, 'dao_steps': steps}
    if kind in ('refused', 'residue', 'rust-only'):
        record['candidate_input_exact'] = images['candidate'] == images['input']
        record['native_changed_bytes'] = (sum(a != b for a, b in zip(images['input'], images['native']))
                                          + abs(len(images['input']) - len(images['native'])))
        require(record['candidate_input_exact'], 'Rust refusal changed its input')
    return record


def native_residue(case, before: bytes, after: bytes, raw_before: dict) -> list[dict]:
    """Exact bytes a native refusal left behind, as recorded in `native_residue`.

    Entries are either {offset, before, after} or symbolic
    {table, counter: row_count|entry_count, index?, delta} and {offset, delta}.
    """
    expected = bytearray(before)
    for entry in case['native_residue']:
        if 'table' in entry:
            d = raw_before['tables'][entry['table']]['definition']
            offset = d['row_count_offset'] if entry['counter'] == 'row_count' else \
                d['physical_indexes'][entry.get('index', 0)]['entry_count_offset']
            value = int.from_bytes(before[offset:offset + 4], 'little') + entry['delta']
            expected[offset:offset + 4] = value.to_bytes(4, 'little')
        elif 'delta' in entry:
            expected[entry['offset']] = before[entry['offset']] + entry['delta']
        else:
            require(before[entry['offset']] == entry['before'], f'native residue source byte {entry["offset"]}')
            expected[entry['offset']] = entry['after']
    require(len(after) == len(before) and after == bytes(expected), 'exact recorded native refusal residue: ' +
            str([(i, a, b) for i, (a, b) in enumerate(zip(before, after)) if a != b][:8]))
    return [{'offset': i, 'before': a, 'native': b} for i, (a, b) in enumerate(zip(before, after)) if a != b]


# --- Raw structure -------------------------------------------------------------------

def definition_bytes(d):
    """Definition body with pointers masked; they are checked through maps and trees."""
    body = bytearray.fromhex(d['body_hex'])
    body[4:8] = bytes(4)
    body[35:43] = bytes(8)
    offset = 43 + 8 * len(d['physical_indexes'])
    for column in d['columns']:
        if column['storage'] == 'variable':
            body[offset + 14:offset + 16] = bytes(2)
        offset += 18
    for column in d['columns']:
        offset += 1 + len(column['name'].encode('cp1252'))
    for _ in d['physical_indexes']:
        for slot in range(10):
            start = offset + slot * 3
            if body[start:start + 2] == b'\xff\xff':
                body[start + 2] = 0
        body[offset + 30:offset + 38] = bytes(8)
        offset += 39
    offset += 20 * len(d['logical_indexes'])
    for index in d['logical_indexes']:
        offset += 1 + len(index['name'].encode('cp1252'))
    for _ in d['long_value_maps']:
        body[offset + 2:offset + 10] = bytes(8)
        offset += 10
    require(offset + 2 == len(body), 'complete logical definition coverage')
    return body.hex()


def semantics(raw, case):
    """Placement-independent raw content of every table, catalog dates masked for edited objects."""
    dates = set(case.get('dates', []))
    for step in case['steps']:
        request = step['request']
        if 'relationship' in request:
            dates.add(request['relationship']['name'])
        if request['operation'] in ('drop_relationship', 'replace_relationship'):
            dates.add(request['name'])
    tables = {}
    for name, table in raw['tables'].items():
        d = table['definition']
        rows = copy.deepcopy([r['values'] for r in table['rows']])
        if name == 'MSysObjects':
            for row in rows:
                if row['Name'] in dates:
                    row['DateCreate'] = row['DateUpdate'] = '<mutated object date>'
        columns = copy.deepcopy(d['columns'])
        for column in columns:
            if column['storage'] == 'variable':
                raw_column = bytearray.fromhex(column['raw_hex'])
                raw_column[14:16] = bytes(2)
                column['raw_hex'] = raw_column.hex()
        tables[name] = {
            'definition_bytes': definition_bytes(d), 'columns': columns,
            'storage_high_water': d['storage_high_water'], 'variable_high_water': d['variable_high_water'],
            'marker': d['marker'], 'header_unknown_hex': d['header_unknown_hex'], 'row_count': d['row_count'],
            'logical_indexes': [{k: i[k] for k in ('name', 'selector', 'physical_index', 'class', 'raw_hex')}
                                for i in d['logical_indexes']],
            'indexes': [{k: i[k] for k in ('index', 'keys', 'flags', 'prefix_hex', 'entry_count', 'live_distinct')}
                        | {'keys_hex': [bytes.fromhex(e)[:-4].hex() for e in i['entries_hex']]} for i in table['indexes']],
            'rows': sorted(rows, key=canonical),
            'payload_columns': sorted(g['storage_id'] for g in d['long_value_maps']),
        }
    return tables


def affected(before, case):
    tables, objects, relations = set(), set(), set()
    relationship_rows = [r['values'] for r in before['tables']['MSysRelationships']['rows']]
    for step in case['steps']:
        request = step['request']
        kind = request['operation']
        table = None
        if 'table' in request:
            table = request['table']['name'] if isinstance(request['table'], dict) else request['table']
            tables.add(table)
            objects.add(table)
        if kind == 'rename_table':
            tables.add(request['name'])
            objects.add(request['name'])
        if 'relationship' in request:
            relationship = request['relationship']
            relations.add(relationship['name'])
            objects.add(relationship['name'])
            tables.update((relationship['parent']['table'], relationship['child']['table']))
        if kind in ('drop_relationship', 'replace_relationship'):
            relations.add(request['name'])
            objects.add(request['name'])
        for row in relationship_rows:
            selected = row['szRelationship'] in relations
            if kind == 'drop_table' and table in (row['szObject'], row['szReferencedObject']):
                selected = True
                relations.add(row['szRelationship'])
                objects.add(row['szRelationship'])
            if selected:
                tables.update((row['szObject'], row['szReferencedObject']))
            if kind in ('rename_table', 'rename_column') and table in (row['szObject'], row['szReferencedObject']):
                relations.add(row['szRelationship'])
    tables.update(case.get('affected_tables', []))
    objects.update(tables)
    return tables, objects, relations


def pages_equal(left, right, number):
    return left[number * PAGE:(number + 1) * PAGE] == right[number * PAGE:(number + 1) * PAGE]


def preserve(before, after, before_bytes, after_bytes, case):
    """Everything the edit does not name keeps its exact storage (EXP-0297)."""
    require(before_bytes[:PAGE] == after_bytes[:PAGE], 'complete original database header')
    tables, objects, relations = affected(before, case)
    preserved_pages, preserved_rows, preserved_objects = set(), [], []
    for name, old in before['tables'].items():
        if name.startswith('MSys') or name in tables:
            continue
        require(name in after['tables'] and old == after['tables'][name], 'unrelated complete table: ' + name)
        pages = set(old['definition']['pages'])
        for role, record in before['maps'].items():
            if role.startswith(name + '/'):
                require(record == after['maps'][role], 'unrelated map: ' + role)
                pages.update(record['members'])
                pages.update(p for p in record['record']['references'] if p)
        for number in pages:
            require(pages_equal(before_bytes, after_bytes, number), f'unrelated page: {number}')
        preserved_pages.update(pages)
    old_catalog = {r['values']['Id']: r for r in before['tables']['MSysObjects']['rows']}
    new_catalog = {r['values']['Id']: r for r in after['tables']['MSysObjects']['rows']}
    affected_ids = {i for i, r in old_catalog.items() if r['values']['Name'] in objects}
    replaced = {s['request']['name'] for s in case['steps'] if s['request']['operation'] == 'replace_relationship'}
    for ident, row in old_catalog.items():
        if ident not in affected_ids:
            require(ident in new_catalog and row == new_catalog[ident], 'complete unrelated catalog row: ' + row['values']['Name'])
            preserved_objects.append(row['values']['Name'])
        elif ident in new_catalog and row['values']['Name'] not in replaced:
            for field in ('DateCreate', 'DateUpdate'):
                require(row['values'][field] == new_catalog[ident]['values'][field], 'existing object timestamp: ' + row['values']['Name'])
    for name in ('MSysQueries', 'MSysACEs', 'MSysRelationships'):
        old, new = before['tables'][name], after['tables'][name]
        if name == 'MSysQueries':
            require(old == new, 'complete saved-query storage and definition')
            query_pages = set(old['definition']['pages'])
            for role, record in before['maps'].items():
                if role.startswith('MSysQueries/'):
                    require(record == after['maps'][role], 'saved-query map record')
                    query_pages.update(record['members'])
                    query_pages.update(p for p in record['record']['references'] if p)
            for number in query_pages:
                require(pages_equal(before_bytes, after_bytes, number), 'complete saved-query owned page')
            preserved_pages.update(query_pages)
        for row in old['rows']:
            if name == 'MSysACEs' and row['values']['ObjectId'] in affected_ids:
                continue
            if name == 'MSysRelationships' and row['values']['szRelationship'] in relations:
                continue
            require(row in new['rows'], 'unrelated raw system row: ' + name)
            preserved_rows.append({'table': name, 'locator': row['locator'], 'sha256': structure.sha(bytes.fromhex(row['raw_hex']))})
    # Schema-only edits keep the existing row bodies, including deleted fields.
    renames = {s['request']['table']: s['request']['name'] for s in case['steps'] if s['request']['operation'] == 'rename_table'}
    rewritten, auto_tables = set(), set()
    for step in case['steps']:
        request = step['request']
        if request['operation'] in ('update', 'replace', 'delete'):
            rewritten.add((request['table'], request['row']['page'], request['row']['slot']))
        if request['operation'] == 'create_column' and request['column']['type'] == 'auto_increment':
            auto_tables.add(request['table'])
    for name, old in before['tables'].items():
        # Cascaded rows are compared with the native image by semantics().
        if name.startswith('MSys') or name in auto_tables or name in case.get('affected_tables', []):
            continue
        new = after['tables'].get(renames.get(name, name))
        if new is None:
            continue
        indexed = {(r['locator']['page'], r['locator']['row']): r for r in new['rows']}
        for row in old['rows']:
            key = (row['locator']['page'], row['locator']['row'])
            if (name, *key) in rewritten:
                continue
            require(key in indexed and row['raw_hex'] == indexed[key]['raw_hex'], 'surviving row bytes: ' + name)
    # Surviving payload columns keep complete descriptor storage and allocation.
    for role, record in before['maps'].items():
        if '/lval/' not in role or role.startswith('MSys') or role not in after['maps']:
            continue
        require(record == after['maps'][role], 'surviving payload allocation: ' + role)
        for number in record['members']:
            require(pages_equal(before_bytes, after_bytes, number), 'surviving payload page')
    return {'unrelated_pages': sorted(preserved_pages), 'unrelated_catalog_objects': sorted(preserved_objects),
            'unrelated_system_rows': preserved_rows, 'affected_tables': sorted(tables)}


def placements(case, candidate_maps, native_maps, allow_global=True):
    """Maps may differ only in page placement, and only for recorded roles.

    `placement_roles` lists roles whose members may differ with equal capacity, or maps a
    role to its recorded [candidate, native] member counts.
    """
    allowed = case.get('placement_roles', [])
    capacities = allowed if isinstance(allowed, dict) else {role: None for role in allowed}
    require(set(candidate_maps) == set(native_maps), 'complete map role inventory')
    found = []
    for role, a in candidate_maps.items():
        b = native_maps[role]
        if a == b:
            continue
        require((allow_global and role == 'global') or role in capacities, 'unlisted placement: ' + role)
        for key in ('kind', 'length', 'start', 'references'):
            require(a['record'][key] == b['record'][key], f'map framing: {role}/{key}')
        if role != 'global':
            counts = [len(a['members']), len(b['members'])]
            recorded = capacities[role]
            require(counts == recorded if recorded else counts[0] == counts[1], 'recorded role capacity: ' + role)
        found.append({'role': role, 'candidate': a, 'native': b})
    return found


def raw_structure(case, raws, images) -> dict:
    """Raw semantics, placement and preservation of the candidate against the native edit.

    A native refusal with recorded residue compares the candidate with its input instead.
    """
    if refused(case):
        require(images['candidate'] == images['input'], 'whole-file Rust refusal')
    residue = None
    right = raws['native']
    if 'native_residue' in case:
        residue = native_residue(case, images['input'], images['native'], raws['input'])
        right = raws['input']
    found = differences(semantics(raws['candidate'], case), semantics(right, case))
    require(not found, 'raw semantic difference: ' + str(brief(found)))
    moved = placements(case, raws['candidate']['maps'], right['maps'])
    kept = preserve(raws['input'], raws['candidate'], images['input'], images['candidate'], case)
    unused_offsets, unused_slots = [], []
    for name, table in raws['candidate']['tables'].items():
        other = right['tables'][name]['definition']
        for a, b in zip(table['definition']['columns'], other['columns']):
            if a['raw_hex'] != b['raw_hex']:
                require(a['storage'] == b['storage'] == 'variable', 'only unused variable fixed-offset bytes differ')
                aa, bb = bytes.fromhex(a['raw_hex']), bytes.fromhex(b['raw_hex'])
                require(aa[:14] + aa[16:] == bb[:14] + bb[16:], 'all meaningful column bytes exact')
                unused_offsets.append({'table': name, 'column': a['name'], 'candidate': aa[14:16].hex(), 'native': bb[14:16].hex()})
        for a, b in zip(table['definition']['physical_indexes'], other['physical_indexes']):
            aa, bb = bytes.fromhex(a['raw_hex']), bytes.fromhex(b['raw_hex'])
            for slot in range(10):
                at = slot * 3
                if aa[at:at + 2] == bb[at:at + 2] == b'\xff\xff' and aa[at + 2] != bb[at + 2]:
                    unused_slots.append({'table': name, 'index': a['index'], 'slot': slot, 'candidate': aa[at + 2], 'native': bb[at + 2]})
    return {'preservation': kept, 'placement_differences': moved, 'native_refusal_residue': residue,
            'unused_variable_fixed_offsets': unused_offsets, 'unused_index_slot_directions': unused_slots}


# --- Rust reader ---------------------------------------------------------------------

def reader(cli, path, item) -> dict:
    """`jet3-cli inspect` relationships equal the DAO Relations getters."""
    dao = sorted((r['name'], r['table'], r['foreign_table'], r['attributes'],
                  [(f['name'], f['foreign_name']) for f in sorted(r['fields'], key=lambda f: f['ordinal'])])
                 for r in item['relations'])
    output = subprocess.run([str(cli), 'inspect', str(path)], capture_output=True, text=True, check=True)
    rust = sorted((r['name'], r['parent'], r['child'], r['raw_attributes'], [(f['parent'], f['child']) for f in r['fields']])
                  for r in json.loads(output.stdout)['relationships'])
    require(json.loads(json.dumps(dao)) == json.loads(json.dumps(rust)), 'Rust relationship reader differs from DAO')
    return {'relationships': len(dao)}


# --- New databases -------------------------------------------------------------------

def catalog_row(raw, name):
    row, = [r for r in raw['tables']['MSysObjects']['rows'] if r['values']['Name'] == name]
    return row


def creation_structure(spec, creation, candidate_path, native_path) -> dict:
    """A Rust-created database against a native SQL-built one (EXP-0308).

    Catalog dates differ by construction. Native SQL-created tables carry DAO's default
    property payload (`native_default_properties`), which Rust does not write.
    """
    candidate, native = structure.observe(candidate_path), structure.observe(native_path)
    tables = {t['name'] for t in creation['request']['tables']}
    case = {'name': creation['name'], 'steps': []}
    left, right = semantics(candidate, case), semantics(native, case)
    left.pop('MSysObjects')
    right.pop('MSysObjects')
    found = differences(left, right)
    require(not found, 'raw table/index difference: ' + str(brief(found)))

    def comparable(raw):
        rows = []
        for source in raw['tables']['MSysObjects']['rows']:
            row = copy.deepcopy(source['values'])
            row['DateCreate'] = row['DateUpdate'] = '<creation time>'
            if row['Name'] in tables:
                row.pop('LvProp')
            rows.append(row)
        return sorted(rows, key=canonical)

    require(comparable(candidate) == comparable(native), 'catalog identity or non-date fields')
    defaults = spec['native_default_properties']
    for table in tables:
        ours, theirs = catalog_row(candidate, table)['values']['LvProp'], catalog_row(native, table)['values']['LvProp']
        require(ours is None and theirs is not None, 'expected native creation default properties: ' + table)
        payload = bytes.fromhex(theirs)
        require(len(payload) == defaults['length'] and structure.sha(payload) == defaults['sha256'],
                'exact native SQL table default properties: ' + table)
    moved = placements(creation, candidate['maps'], native['maps'], allow_global=False)
    return {'placement_roles': [m['role'] for m in moved], 'native_default_property_tables': sorted(tables)}


def property_payload(data: bytes, table: str):
    """The table's LvProp payload and its storage class: header word, fragment lengths and
    membership of the LvProp available map (locators themselves are placement)."""
    catalog = structure.tables(data)['MSysObjects']
    group, = [g for g in catalog['definition']['long_value_maps'] if g['column_name'] == 'LvProp']
    _, owned = structure.map_record(data, group['owned'], 'catalog LvProp owned')
    _, available = structure.map_record(data, group['available'], 'catalog LvProp available')
    row, = [r for r in structure.rows(data, catalog) if r['values']['Name'] == table]
    descriptor = bytes.fromhex(row['descriptors']['LvProp'])
    chain = []
    payload = structure.payload(data, descriptor, owned, set(), chain)
    return payload, {'word': descriptor[:4].hex(), 'fragments': [[length, number in available] for number, _, length in chain]}


def lvprop(tables, candidate: bytes, native: bytes) -> list[dict]:
    """Raw LvProp payloads and storage classes of the named tables are equal."""
    results = []
    for table in sorted(tables):
        ours, our_storage = property_payload(candidate, table)
        theirs, their_storage = property_payload(native, table)
        results.append({'table': table, 'length': len(ours), 'candidate_sha256': structure.sha(ours),
                        'native_sha256': structure.sha(theirs), 'candidate_storage': our_storage,
                        'native_storage': their_storage})
        require(ours == theirs, 'LvProp payload: ' + table)
        require(our_storage == their_storage, f'LvProp storage class: {table} {our_storage} {their_storage}')
    return results


def creation_lvprop(spec, creation, candidate_path, native_path) -> list[dict]:
    return lvprop([t['name'] for t in creation['request']['tables']], candidate_path.read_bytes(), native_path.read_bytes())


CREATION_CHECKS = {'creation-structure': creation_structure, 'lvprop': creation_lvprop}


# --- Native continuations ------------------------------------------------------------

def row_model(rows: dict, before: list[dict]) -> dict:
    """Rows keyed by `rows.key` after the declared update/insert/delete."""
    key = rows['key']
    result = {row[key]: dict(row) for row in before}
    for ident, change in rows.get('update', {}).items():
        result[int(ident)].update(change)
    for row in rows.get('insert', []):
        result[row[key]] = dict(row)
    for ident in rows.get('delete', []):
        result.pop(ident)
    return result


def dao_rows(continuation, item, expected: dict) -> list:
    """The DAO table scan holds exactly the modelled rows; returns its physical order."""
    rows = continuation['rows']
    table, = [t for t in item['tables'] if t['name'] == rows['table']]
    require(sorted(table['rows'], key=canonical) == sorted(expected.values(), key=canonical),
            'DAO continuation row model: ' + item['file'])
    return [row[rows['key']] for row in table['rows']]


def continuation(spec, raws, images) -> dict:
    """Both lineages after the same native writes (EXP-0310).

    For each side: exactly the modelled rows, one header counter step per write, and
    complete preservation of everything the writes do not touch. Then equal raw semantics.
    `raws[side]` and `images[side]` are (before, after) pairs.
    """
    rows = spec['rows']
    kept, headers, after = {}, {}, {}
    for side in ('candidate', 'native'):
        (before, observed), (prior, data) = raws[side], images[side]
        values = [r['values'] for r in before['tables'][rows['table']]['rows']]
        expected = row_model(rows, values)
        actual = {r['values'][rows['key']]: r['values'] for r in observed['tables'][rows['table']]['rows']}
        require(actual == expected, f'{side}: complete continuation row model')
        header = [{'offset': i, 'before': a, 'after': b} for i, (a, b) in enumerate(zip(prior[:PAGE], data[:PAGE])) if a != b]
        require(header == [{'offset': 1538, 'before': prior[1538], 'after': prior[1538] + len(spec['steps'])}],
                f'{side}: native header counter for {len(spec["steps"])} accepted writes')
        headers[side] = header
        requests = []
        for touched in spec.get('touched', []):
            row, = [r for r in before['tables'][touched['table']]['rows'] if r['values'][rows['key']] == touched['id']]
            requests.append({'request': {'operation': touched['operation'], 'table': touched['table'],
                                         'row': {'page': row['locator']['page'], 'slot': row['locator']['row']}}})
        kept[side] = preserve(before, observed, data[:PAGE] + prior[PAGE:], data, {'name': spec['name'], 'steps': requests})
        after[side] = observed
    case = {'name': spec['name'], 'steps': [{'request': {'operation': 'update', 'table': rows['table']}}]}
    found = differences(semantics(after['candidate'], case), semantics(after['native'], case))
    require(not found, 'raw continuation semantics: ' + str(brief(found)))
    return {'preservation': kept, 'header_changes': headers}
