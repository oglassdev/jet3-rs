#!/usr/bin/env python3
"""Raw schema-edit checks using original Jet 3 observations (EXP-0297)."""
from __future__ import annotations
import argparse, copy, hashlib, json, sys
from pathlib import Path


def require(value, message):
    if not value:
        raise AssertionError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], 'little')


def u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], 'little')


def locator(raw, offset):
    return {'page': int.from_bytes(raw[offset + 1:offset + 4], 'little'), 'row': raw[offset]}


def directory(image, page):
    count = u16(image, 8)
    require(10 + 2 * count <= 2048, 'bounded directory')
    rows, previous = [], 2048
    for ordinal in range(count):
        word = u16(image, 10 + 2 * ordinal)
        if word == 0xc000:
            rows.append({'row': ordinal, 'start': previous, 'end': previous, 'hidden': True, 'overflow': True})
            continue
        start = word & 0x1fff
        require(not word & 0x2000 and 10 + 2 * count <= start <= previous, f'directory {page}/{ordinal}')
        require(word & 0xc000 != 0xc000 or start == previous, 'deleted slot must be empty')
        rows.append({'row': ordinal, 'start': start, 'end': previous,
                     'hidden': bool(word & 0x8000), 'overflow': bool(word & 0x4000)})
        previous = start
    return rows


def definition(data, root):
    pages, body, current = [], bytearray(), root
    while current:
        require(current not in pages and current < len(data) // 2048, 'definition chain')
        image = data[current * 2048:(current + 1) * 2048]
        require(image[:4] == catalog.DEFINITION_PREFIX, 'definition prefix')
        body += image if not pages else image[8:]
        pages.append(current); current = u32(image, 4)
    total = u32(body, 8); require(43 <= total <= len(body), 'definition length')
    body = bytes(body[:total])
    high, var_high, live, logical_count, physical_count = [u16(body, i) for i in (21, 23, 25, 27, 31)]
    require(live <= high <= 255 and var_high <= high, 'definition high water')
    def file_offset(position):
        if position < 2048: return root * 2048 + position
        index, within = divmod(position - 2048, 2040)
        return pages[index + 1] * 2048 + 8 + within
    offset = 43
    prefixes = [body[offset + 8 * i:offset + 8 * (i + 1)] for i in range(physical_count)]
    offset += 8 * physical_count
    columns = []
    for ordinal in range(live):
        raw = body[offset:offset + 18]; offset += 18
        require(len(raw) == 18 and raw[0] in catalog.PHYSICAL_TYPES, 'column record')
        kind = catalog.PHYSICAL_TYPES[raw[0]]; storage = 'variable' if raw[13] & 7 == 2 else 'fixed'
        require(raw[13] & 7 in (2, 3, 7), 'column storage flags')
        columns.append({'ordinal': ordinal, 'storage_id': u16(raw, 1), 'ordinal_repeat': u16(raw, 5),
                        'type': kind, 'type_code': raw[0], 'class': raw[13], 'constant': u16(raw, 7),
                        'context_hex': raw[9:13].hex(), 'fixed_offset': u16(raw, 14) if storage == 'fixed' else None,
                        'size': u16(raw, 16), 'variable_index': u16(raw, 3), 'storage': storage, 'raw_hex': raw.hex()})
    require(len({c['storage_id'] for c in columns}) == live and all(c['storage_id'] < high for c in columns), 'unique stored IDs')
    variable = [c['variable_index'] for c in columns if c['storage'] == 'variable']
    require(len(set(variable)) == len(variable) and all(v < var_high for v in variable), 'variable high water')
    def take_name():
        nonlocal offset
        length = body[offset]; offset += 1
        value = body[offset:offset + length].decode('cp1252'); offset += length
        require(len(value) == length, 'name bound'); return value
    for c in columns: c['name'] = take_name()
    by_id = {c['storage_id']: c for c in columns}
    physical = []
    for i in range(physical_count):
        raw = body[offset:offset + 39]; offset += 39; keys = []
        for slot in range(10):
            storage_id = u16(raw, slot * 3)
            if storage_id == 65535: continue
            require(slot == len(keys) and storage_id in by_id and raw[slot * 3 + 2] in (0, 1), 'physical keys')
            keys.append({'column': by_id[storage_id]['ordinal'], 'storage_id': storage_id, 'direction': raw[slot * 3 + 2]})
        require(keys, 'nonempty index')
        physical.append({'index': i, 'keys': keys, 'flags': raw[38], 'root': u32(raw, 34), 'map': locator(raw, 30),
                         'entry_count': u32(prefixes[i], 4), 'entry_count_offset': file_offset(43 + i * 8 + 4),
                         'prefix_hex': prefixes[i][:4].hex(), 'raw_hex': raw.hex()})
    logical = []
    for i in range(logical_count):
        raw = body[offset:offset + 20]; offset += 20
        require(len(raw) == 20 and u32(raw, 4) < physical_count, 'logical physical selection')
        logical.append({'class': raw[19], 'selector': u32(raw, 0), 'physical_index': u32(raw, 4), 'raw_hex': raw.hex()})
    require(len({i['selector'] for i in logical}) == logical_count, 'unique logical selectors')
    for entry in logical: entry['name'] = take_name()
    suffix = body[offset:-2]; require(body[-2:] == b'\xff\xff' and len(suffix) % 10 == 0, 'definition suffix')
    groups = []
    for offset in range(0, len(suffix), 10):
        raw = suffix[offset:offset + 10]; storage_id = u16(raw, 0)
        require(storage_id in by_id and by_id[storage_id]['type'] in ('Memo', 'LongBinary'), 'payload map column')
        c = by_id[storage_id]
        groups.append({'column': c['ordinal'], 'storage_id': storage_id, 'column_name': c['name'],
                       'owned': locator(raw, 2), 'available': locator(raw, 6)})
    return {'root': root, 'pages': pages, 'logical_length': total, 'row_count': u32(body, 12),
            'row_count_offset': file_offset(12), 'header_unknown_hex': body[16:20].hex() + body[33:35].hex(),
            'marker': body[20], 'storage_high_water': high, 'variable_high_water': var_high,
            'columns': columns, 'logical_indexes': logical, 'physical_indexes': physical,
            'maps': {'owned': locator(body, 35), 'available': locator(body, 39)},
            'long_value_maps': groups, 'suffix_hex': suffix.hex(), 'body_hex': body.hex()}


def fields(raw, columns):
    count = raw[0]; presence_len = (count + 7) // 8
    require(count > 0 and len(raw) > presence_len, 'stored row framing')
    presence = raw[-presence_len:]; trailer = len(raw) - presence_len
    live_variables = [c for c in columns if c['storage'] == 'variable' and c['storage_id'] < count]
    # Deleted variable slots remain in the old row. The definition high-water tells
    # whether that row format contains a trailer even when no live slot remains.
    variable_format = any(c['variable_high_water'] for c in columns)
    bounds = []
    if variable_format:
        count_pos = trailer - 1; variable_count = raw[count_pos]
        jumps = (len(raw) - 1) // 256
        end = count_pos - jumps - variable_count - 1
        require(end >= 1, 'row trailer')
        lows = list(reversed(raw[end:end + variable_count + 1]))
        jump_values = raw[end + variable_count + 1:count_pos]
        bounds = [low + 256 * sum(j != 255 and j <= i for j in jump_values) for i, low in enumerate(lows)]
        if variable_count == 255: bounds[-1] = end
        require(bounds[-1] == end and all(a <= b for a, b in zip(bounds, bounds[1:])), 'row offsets')
        fixed_end = bounds[0]
    else:
        fixed_end = trailer
    values = []
    for c in columns:
        stored_id = c['storage_id']; present = stored_id < count and bool(presence[stored_id // 8] & (1 << (stored_id % 8)))
        if c['type'] == 'Boolean': values.append(bool(present)); continue
        if not present: values.append(None); continue
        if c['storage'] == 'fixed':
            start = 1 + c['fixed_offset']; end = start + c['size']
            require(end <= fixed_end, 'present fixed field bound'); value = raw[start:end]
        else:
            slot = c['variable_index']; require(slot + 1 < len(bounds), 'present variable slot')
            value = raw[bounds[slot]:bounds[slot + 1]]
        values.append(value)
    return values


def payload(data, field, owned, reached):
    require(len(field) >= 12, 'payload descriptor')
    word = u32(field, 0); length, flags = word & 0xffffff, word & 0xff000000
    require(field[8:12] == bytes(4), 'payload reserved bytes')
    if flags == 0x80000000:
        require(field[4:8] == bytes(4) and len(field) == 12 + length, 'inline payload')
        return field[12:]
    require(flags in (0, 0x40000000) and len(field) == 12, 'external payload')
    loc = locator(field, 4); value = bytearray()
    while loc['page']:
        key = (loc['page'], loc['row'])
        require(loc['page'] in owned and key not in reached, 'distinct owned payload')
        reached.add(key)
        image = catalog._page(data, loc['page'], 'payload')
        require(image[:2] == b'\x01\x01' and image[4:8] == b'LVAL', 'payload page')
        entry = directory(image, loc['page'])[loc['row']]
        require(not entry['hidden'] and not entry['overflow'], 'live payload fragment')
        fragment = image[entry['start']:entry['end']]
        if flags == 0x40000000: value += fragment; break
        require(len(fragment) > 4, 'payload fragment framing'); value += fragment[4:]
        require(len(value) <= length, 'payload length bound'); loc = locator(fragment, 0)
    require(len(value) == length, 'complete payload length')
    return bytes(value)


def rows(data, table):
    d = table['definition']; columns = [dict(c, variable_high_water=d['variable_high_water']) for c in d['columns']]
    owned = {g['column']: set(catalog._locator_pages(data, g['owned'], 'payload owned')) for g in d['long_value_maps']}
    reached = {i: set() for i in owned}; result, physical, targets = [], set(), set()
    for p in table['data_pages']:
        image = catalog._page(data, p, 'data')
        for e in directory(image, p):
            if e['start'] < e['end'] and not e['overflow']: physical.add((p, e['row']))
            if e['hidden']: continue
            loc = (p, e['row']); entry, current, page = e, p, image; seen = set()
            while True:
                require((current, entry['row']) not in seen, 'row cycle'); seen.add((current, entry['row']))
                raw = page[entry['start']:entry['end']]
                if not entry['overflow']: break
                require(len(raw) == 4, 'row link'); next_loc = locator(raw, 0); current = next_loc['page']
                require(current in table['data_pages'], 'row link owner'); page = catalog._page(data, current, 'row link')
                entry = directory(page, current)[next_loc['row']]; require(entry['hidden'], 'hidden link target')
            target = (current, entry['row']); require(target not in targets, 'unique row storage'); targets.add(target)
            decoded, descriptors = {}, {}
            for c, value in zip(columns, fields(raw, columns)):
                if c['type'] == 'Boolean' or value is None: decoded[c['name']] = value
                elif c['type'] in ('Memo', 'LongBinary'):
                    require(c['ordinal'] in owned, 'payload map exists')
                    decoded[c['name']] = payload(data, value, owned[c['ordinal']], reached[c['ordinal']]).hex()
                    descriptors[c['name']] = value.hex()
                else: decoded[c['name']] = catalog._decode_value(c, value, True)
            result.append({'locator': {'page': loc[0], 'row': loc[1]}, 'storage': {'page': target[0], 'row': target[1]},
                           'values': decoded, 'descriptors': descriptors, 'raw_hex': raw.hex()})
    require(targets == physical, 'all stored rows reachable')
    for ordinal, members in owned.items():
        active = set()
        for p in members:
            image = catalog._page(data, p, 'owned payload')
            require(image[:2] == b'\x01\x01' and image[4:8] == b'LVAL', 'owned payload page')
            for e in directory(image, p):
                if not e['hidden']: active.add((p, e['row']))
        require(active == reached[ordinal], f'complete payload reachability {table["name"]}/{ordinal}')
    return result


def index_key(value, column, descending):
    kind = column['type_code']
    if kind == 10 and value is not None: value = value.encode('cp1252').hex()
    if isinstance(value, dict):
        raw = bytes.fromhex(value['raw_hex'])
        if kind in (3, 4, 5): value = int.from_bytes(raw, 'little', signed=True)
        else: raise AssertionError('unmodeled index type')
    return numeric.component(value, kind, descending)


def observe(path, allowed_count_deltas=None):
    data = path.read_bytes(); require(len(data) % 2048 == 0, 'complete pages')
    analysis = catalog.analyze_checkpoint(data)
    named = {t['name']: t for t in analysis['tables'].values()}; tables = {}; maps = {}
    def add_map(role, loc):
        record, members = allocation.map_record(data, loc, role)
        maps[role] = {'record': record, 'members': sorted(members)}
        return set(members)
    free = add_map('global', {'page': 1, 'row': 0}); metadata = {0, 1}; claimed = {}
    for name, table in named.items():
        d = table['definition']; metadata.update(d['pages']); rr = rows(data, table); physical = []
        require(d['row_count'] - len(rr) == (allowed_count_deltas or {}).get(name, 0), f'declared/live table count {name}')
        groups = [('table', d['maps']['owned'], d['maps']['available'])]
        groups += [(f'index/{i["index"]}', i['map'], None) for i in d['physical_indexes']]
        groups += [(f'lval/{g["column_name"]}', g['owned'], g['available']) for g in d['long_value_maps']]
        for role, ownloc, avail in groups:
            members = add_map(name + '/' + role + '/owned', ownloc)
            require(not members & free, 'owned globally allocated')
            for p in members:
                require(p not in claimed, 'unique page ownership'); claimed[p] = name + '/' + role
            if avail: require(add_map(name + '/' + role + '/available', avail) <= members, 'availability subset')
        for i in d['physical_indexes']:
            cc = [d['columns'][k['column']] for k in i['keys']]
            nodes, entries = trees.tree(data, i['root'], d['root'], [(c['type_code'], k['direction'] == 0) for c, k in zip(cc, i['keys'])])
            expected = []
            for row in rr:
                if i['flags'] & 2 and any(row['values'][c['name']] is None for c in cc): continue
                key = numeric.shorten_key(b''.join(index_key(row['values'][c['name']], c, k['direction'] == 0) for c, k in zip(cc, i['keys'])))
                expected.append(key + row['locator']['page'].to_bytes(3, 'big') + bytes([row['locator']['row']]))
            require(entries == sorted(expected), f'{path.name}/{name}/{i["index"]} complete physical keys')
            members = set(maps[f'{name}/index/{i["index"]}/owned']['members'])
            require({n['page'] for n in nodes} <= members, 'all tree pages owned')
            for page_no in members:
                image = catalog._page(data, page_no, 'owned index')
                require(image[0] in (3,4) and u32(image,4) == d['root'], 'all owned index pages have correct owner')
            physical.append(dict(i, nodes=nodes, entries_hex=[e.hex() for e in entries], live_distinct=len({e[:-4] for e in entries})))
        tables[name] = {'definition': d, 'rows': rr, 'indexes': physical}
    locs, refs = [], []
    for record in maps.values():
        loc = record['record']['locator']; locs.append((loc['page'], loc['row'])); metadata.add(loc['page'])
        for p in record['record']['references']:
            if p: refs.append(p); metadata.add(p)
    require(len(locs) == len(set(locs)) and len(refs) == len(set(refs)), 'unique maps/bitmaps')
    active_maps = set()
    for page_no in {p for p,_ in locs}:
        for entry in directory(catalog._page(data,page_no,'map container'),page_no):
            if not entry['hidden'] and not entry['overflow']: active_maps.add((page_no,entry['row']))
    require(active_maps - set(locs) == {(1,1)}, 'only native empty global reserve map is unreferenced')
    require(catalog._locator_row(data, {'page':1,'row':1}, 'global reserve') == bytes(133), 'native empty global reserve bytes')
    require(not metadata & free and not metadata & set(claimed), 'metadata allocation separation')
    require(free | metadata | set(claimed) == set(range(len(data) // 2048)), 'complete page allocation classification')
    return {'identity': {'size': len(data), 'sha256': sha(data)}, 'page0_hex': data[:2048].hex(), 'tables': tables,
            'maps': maps, 'free_pages': sorted(free), 'metadata_pages': sorted(metadata),
            'owned_pages': {str(p): role for p, role in sorted(claimed.items())}}


def setup(scripts):
    global catalog, allocation, numeric, trees
    sys.path.insert(0, str(scripts))
    import system_catalog as catalog
    import allocation_lifecycle_structure as allocation
    import numeric_index_mutation as numeric
    import numeric_index_mutation_structure as trees
    for module in (catalog, allocation.catalog, trees.catalog):
        module.MAX_PAGES = 8192; module.MAX_ROWS_PER_PAGE = 1019; module.MAX_TABLES = 128; module.MAX_COLUMNS = 255
        module._row_directory = directory; module._definition = definition
    catalog._locator_pages = lambda data, loc, role: allocation.map_record(data, loc, role)[1]
    def table_rows(data, d, pages):
        decoded = rows(data, {'name': str(d['root']), 'definition': d, 'data_pages': pages})
        return [{'page': r['locator']['page'], 'row': r['locator']['row'], 'values': [r['values'][c['name']] for c in d['columns']], 'raw_hex': r['raw_hex']} for r in decoded]
    catalog._table_rows = table_rows


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--scripts', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True); parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(); setup(args.scripts); args.out.mkdir(exist_ok=False)
    manifest = json.loads((args.prepared / 'manifest.json').read_text()); cases = []; failures = []
    for case in manifest['cases']:
        item = {'name': case['name'], 'images': {}}
        for kind, path in [('input', Path(case['input'])), ('candidate', args.prepared / ('candidate-' + case['name'] + '.mdb')), ('native', args.prepared / ('native-' + case['name'] + '.mdb'))]:
            try:
                observed = observe(path, {'MSysObjects': 1, 'MSysACEs': 2} if kind == 'native' and case['name'] == 'relationship-orphan-refusal' else None)
                report = args.out / (kind + '-' + case['name'] + '.json')
                report.write_text(json.dumps(observed, indent=2, sort_keys=True) + '\n')
                item['images'][kind] = {'path': str(report), 'sha256': sha(report.read_bytes())}
            except Exception as error:
                failures.append({'case': case['name'], 'kind': kind, 'error': str(error)})
        cases.append(item)
    result = {'status': 'fail' if failures else 'pass', 'cases': cases, 'failures': failures}
    (args.out / 'STRUCTURE-RESULT.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'failures': len(failures), 'examples': failures[:5]}, indent=2))
    if failures: raise SystemExit(1)

if __name__ == '__main__': main()
