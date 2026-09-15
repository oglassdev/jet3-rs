"""Original bounded raw allocation checks from EXP-0057/0061 and native discovery."""
import hashlib
import importlib.util
from pathlib import Path
import struct


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


catalog = load('allocation_catalog', 'system_catalog.py')
catalog.MAX_PAGES = 25000
catalog.MAX_ROWS_PER_PAGE = 256
index = load('allocation_index', 'numeric_index_mutation_structure.py')
index.catalog = catalog


def require(condition, message):
    if not condition: raise ValueError(message)


def ranges(values):
    result = []
    for value in sorted(values):
        if result and result[-1][1] == value: result[-1][1] += 1
        else: result.append([value, value + 1])
    return result


def map_record(data, locator, role):
    raw = catalog._locator_row(data, locator, role); pages = len(data) // 2048
    members = set(); references = []
    def bits(bitmap, base):
        for offset, byte in enumerate(bitmap):
            for bit in range(8):
                if byte & (1 << bit): members.add(base + offset * 8 + bit)
    if raw[0] == 0:
        require(len(raw) >= 5, 'Complete inline map')
        start = int.from_bytes(raw[1:5], 'little'); bits(raw[5:], start)
    else:
        require(raw[0] == 1 and len(raw) == 133, 'Complete 33-slot indirect map')
        start = 0; references = [int.from_bytes(raw[o:o + 4], 'little') for o in range(1, len(raw), 4)]
        active = [r for r in references if r]
        require(len(active) == len(set(active)) and references[:len(active)] == active, 'Distinct active bitmap prefix and zero tail')
        for slot, reference in enumerate(references):
            if not reference: continue
            require(0 < reference < pages and reference != locator['page'], 'Bounded bitmap reference')
            page = catalog._page(data, reference, 'bitmap')
            require(page[:4] == b'\x05\x01\x00\x00', 'Extended bitmap header')
            bits(page[4:], slot * 16352)
    beyond = {p for p in members if p >= pages}
    if role == 'global':
        end = start + (len(raw) - 5) * 8 if raw[0] == 0 else len(active) * 16352
        require(start == 0 and beyond == set(range(pages, max(pages, end))), 'Global represented tail beyond EOF is free')
    require(role == 'global' or not beyond, 'Owned/available members are captured')
    members -= beyond
    return dict(locator=locator, kind=raw[0], length=len(raw), start=start, references=references,
                raw_hex=raw.hex(), members=ranges(members), outside_eof=ranges(beyond)), members


catalog._locator_pages = lambda data, locator, what: map_record(data, locator, what)[1]


def tables(data):
    definition, _, records = catalog._discover_catalog(data)
    name, id = [catalog._ordinal(definition, n) for n in ('Name', 'Id')]
    roots = {r['values'][name]: r['values'][id] for r in records}
    require(all(n in roots for n in ('Items', 'Notes')), 'User table inventory')
    return {name: catalog._definition(data, roots[name]) for name in ('Items', 'Notes')}


def feed(state, value):
    state.update(struct.pack('<i', -1 if value is None else len(value)))
    if value is not None: state.update(value)


def key(values, definition):
    result = b''
    for ordinal, descending in definition['fields']:
        value = values[ordinal]
        component = b'\0' if value is None else b'\x7f' + ((int.from_bytes(value, 'little', signed=True) & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')
        result += bytes(b ^ 255 for b in component) if descending else component
    return result


def payload(data, header, owned, reached):
    raw = bytes.fromhex(header['long_value_header_hex']); require(len(raw) == 12 and raw[8:] == bytes(4), 'External payload header')
    word = int.from_bytes(raw[:4], 'little'); length = word & 0xffffff; flags = word & 0xff000000
    require(length == 1800 and flags in (0, 0x40000000), 'Finite external payload kind/length')
    page, slot = int.from_bytes(raw[5:8], 'little'), raw[4]; value = bytearray()
    while page:
        require(page in owned and (page, slot) not in reached, 'Distinct owned payload locator')
        reached.add((page, slot)); image = catalog._page(data, page, 'payload')
        require(image[:2] == b'\x01\x01' and image[4:8] == b'LVAL', 'Payload page owner')
        entry = next((e for e in catalog._row_directory(image, page) if e['row'] == slot), None)
        require(entry is not None and not entry['hidden'] and not entry['overflow'], 'Live payload slot')
        fragment = image[entry['start']:entry['end']]
        if flags == 0x40000000: value.extend(fragment); break
        require(len(fragment) > 4, 'Nonempty chained fragment'); value.extend(fragment[4:])
        require(len(value) <= length, 'Bounded payload chain')
        page, slot = int.from_bytes(fragment[1:4], 'little'), fragment[0]
    require(len(value) == length, 'Complete external payload bytes')
    return bytes(value)


def inspect(data, case, expected, receipt=None, receipt_root=None):
    definitions = tables(data); table = definitions['Items']; notes = definitions['Notes']
    require([[c['name'], c['type'], c['size']] for c in table['columns']] ==
            [[name, catalog.PHYSICAL_TYPES[kind], size] for name, kind, size, _ in case['fields']], 'Complete raw schema')
    locations = dict(global_map=dict(page=1, row=0), table_owned=table['maps']['owned'], table_available=table['maps']['available'])
    locations.update(notes_owned=notes['maps']['owned'], notes_available=notes['maps']['available'])
    for p in table['physical_indexes']: locations['index' + str(p['index'])] = p['map']
    for group in table['long_value_maps']:
        for role in ('owned', 'available'): locations[f"lval{group['column']}_{role}"] = group[role]
    locations['global'] = locations.pop('global_map')
    require(len({(v['page'], v['row']) for v in locations.values()}) == len(locations), 'Distinct map rows')
    maps = {}; members = {}; bitmaps = set()
    for role, locator in locations.items():
        maps[role], members[role] = map_record(data, locator, role)
        refs = {p for p in maps[role]['references'] if p}
        require(not refs & bitmaps, 'Independent role bitmap pages'); bitmaps |= refs
    all_owned = set()
    for role, owned in members.items():
        if not (role.endswith('_owned') or role.startswith('index')): continue
        require(not owned & all_owned and not owned & members['global'], 'Disjoint globally allocated ownership: ' + role)
        all_owned |= owned
        if role.endswith('_owned'): require(members[role.replace('_owned', '_available')] <= owned, 'Availability subset')
    metadata = set(table['pages']) | set(notes['pages']) | {p['page'] for p in locations.values()} | bitmaps
    require(not metadata & (members['global'] | all_owned), 'Independent allocated metadata')
    data_pages, misplaced = catalog._table_pages(data, table); require(not misplaced, 'Table owns data, not LVAL pages')
    raw_rows = catalog._table_rows(data, table, data_pages)
    require(table['row_count'] == len(raw_rows) == len(expected), 'Complete live row count')
    decoded = {}; locators = []; reached = {g['column']: set() for g in table['long_value_maps']}
    for row in raw_rows:
        id = row['values'][0]; values = []
        for ordinal, value in enumerate(row['values']):
            kind = case['fields'][ordinal][1]
            if value is None: values.append(None)
            elif kind == 4: values.append(struct.pack('<i', value))
            elif kind == 10: values.append(value.encode('cp1252'))
            else: values.append(payload(data, value, members[f'lval{ordinal}_owned'], reached[ordinal]))
        require(id not in decoded and values == expected.get(id), 'Complete raw row/payload bytes: ' + str(id))
        decoded[id] = values; locators.append([id, row['page'], row['row']])
    require(set(decoded) == set(expected), 'Complete raw Id inventory')
    for ordinal, refs in reached.items():
        active = set()
        for page in members[f'lval{ordinal}_owned']:
            image = catalog._page(data, page, 'owned LVAL')
            for entry in catalog._row_directory(image, page):
                if entry['hidden']: require(entry['overflow'] and entry['start'] == entry['end'], 'Empty LVAL tombstone')
                else: active.add((page, entry['row']))
        require(active == refs, 'Complete active LVAL slot reachability')
    digest = hashlib.sha256()
    for id in sorted(decoded):
        for value in decoded[id]: feed(digest, value)
    logical = {i['name']: i['physical_index'] for i in table['logical_indexes']}
    require(set(logical) == {i['name'] for i in case['indexes']} and len(table['physical_indexes']) == len(logical), 'Complete raw index inventory')
    index_layout = {}; rust_indexes = {}
    if receipt:
        observed = receipt['tables']['Items']['indexes']; names = [i['name'] for i in observed]
        require(len(names) == len(set(names)) and set(names) == set(logical), 'Exact unique Rust index inventory')
        rust_indexes = {i['name']: i for i in observed}
    for definition in case['indexes']:
        logical_record = next(i for i in table['logical_indexes'] if i['name'] == definition['name'])
        require(logical_record['class'] == int(definition['primary']), 'Raw logical index class')
        physical = table['physical_indexes'][logical[definition['name']]]
        require(physical['keys'] == [dict(column=c, direction=int(not d)) for c, d in definition['fields']] and
                physical['flags'] == int(definition['unique']) + 8 * int(definition['required']), 'Raw index metadata')
        nodes, entries = index.tree(data, physical['root'], table['root'], [(4, d) for _, d in definition['fields']])
        wanted = sorted(key(decoded[id], definition) + page.to_bytes(3, 'big') + bytes([slot]) for id, page, slot in locators)
        require(entries == wanted, 'Every physical key/locator: ' + definition['name'])
        owned = members['index' + str(physical['index'])]; visited = {n['page'] for n in nodes}
        require(visited <= owned, 'All index nodes owned')
        for page in owned:
            image = catalog._page(data, page, 'owned index')
            require(image[0] in (3, 4) and int.from_bytes(image[4:8], 'little') == table['root'], 'Owned index kind/owner')
        index_layout[definition['name']] = dict(nodes=sorted(visited), reserved=sorted(owned - visited), depth=max(n['depth'] for n in nodes))
        if receipt:
            actual = rust_indexes[definition['name']]
            require(actual['metadata'] == dict(logical=logical_record['raw_hex'], prefix=physical['prefix_hex'] + physical['entry_count'].to_bytes(4, 'little').hex(), root=physical['root'],
                    flags=physical['flags'], map=[physical['map']['page'], physical['map']['row']],
                    keys=[[k['column'], k['direction']] for k in physical['keys']]), 'Complete Rust index definition receipt')
            require(actual['entries'] == [[e[:-4].hex(), int.from_bytes(e[-4:-1], 'big'), e[-1]] for e in entries] and
                    sorted(actual['nodes']) == sorted(visited) and actual['depth'] == index_layout[definition['name']]['depth'], 'Complete Rust/raw index receipt')
    note_pages, lval = catalog._table_pages(data, notes)
    require(not lval and [r['values'] for r in catalog._table_rows(data, notes, note_pages)] == [[7, 'allocation-control']], 'Complete raw Notes')
    note_owned = set(note_pages) | set(notes['pages']) | {p['page'] for p in notes['maps'].values()}
    note_owned |= {p for role in ('notes_owned', 'notes_available') for p in maps[role]['references'] if p}
    notes_identity = {str(p): hashlib.sha256(data[p * 2048:(p + 1) * 2048]).hexdigest() for p in sorted(note_owned)}
    if receipt:
        require(receipt['pages'] * 2048 == len(data) and set(receipt['tables']) == {'Items', 'Notes'}, 'Complete Rust geometry/inventory')
        for name, definition in definitions.items():
            actual = receipt['tables'][name]
            require(actual['fields'] == [[c['name'], next(k for k, v in catalog.PHYSICAL_TYPES.items() if v == c['type']), c['size'], c['class']] for c in definition['columns']], 'Rust/raw complete column receipt')
            require(actual['count'] == actual['declared_count'] == (len(expected) if name == 'Items' else 1), 'Rust row count')
            expected_hash = digest.hexdigest() if name == 'Items' else canonical_digest([[struct.pack('<i', 7), b'allocation-control']])
            path = receipt_root / actual['stream']
            require(path.name == actual['stream'] and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash, 'Complete Rust canonical row stream')
        require(receipt['tables']['Items']['locators'] == sorted(locators) and receipt['tables']['Notes']['indexes'] == [], 'Rust locators and Notes indexes')
    return dict(pages=len(data) // 2048, maps=maps, indexes=index_layout, digest=digest.hexdigest(),
                count=len(expected), locators=sorted(locators), notes=notes_identity)


def canonical_digest(rows):
    state = hashlib.sha256()
    for row in rows:
        for cell in row: feed(state, cell)
    return state.hexdigest()
