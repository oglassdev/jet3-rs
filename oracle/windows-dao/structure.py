"""Independent Jet 3 structure decoder used by the DAO comparisons.

Every rule here comes from native observations recorded in docs/PROVENANCE.md
(EXP-0057..0077 pages, maps and catalog; EXP-0062/0225/0246 index trees; EXP-0245/0257
row trailers; EXP-0265/0297 schema edits). The decoder is strict: anything outside the
observed grammar raises DecodeError.

Public API:
    page, directory, map_record, locator_row, definition, fields, decode_value, payload,
    tables, rows, tree, observe
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import keys

PAGE = 2048
DEFINITION_PREFIX = b'\x02\x01\x56\x43'
LONG_VALUE_OWNER = b'LVAL'
PHYSICAL_TYPES = {
    1: 'Boolean', 2: 'Byte', 3: 'Integer', 4: 'Long', 5: 'Currency', 6: 'Single', 7: 'Double',
    8: 'Date', 9: 'Binary', 10: 'Text', 11: 'LongBinary', 12: 'Memo', 15: 'GUID',
}


class DecodeError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DecodeError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], 'little')


def u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], 'little')


def locator(raw, offset):
    """A row locator: row byte followed by a three-byte little-endian page."""
    return {'page': int.from_bytes(raw[offset + 1:offset + 4], 'little'), 'row': raw[offset]}


def page(data: bytes, number: int, what: str = 'page') -> bytes:
    require(0 <= number < len(data) // PAGE, f'{what}: page {number} outside the image')
    return data[number * PAGE:(number + 1) * PAGE]


def directory(image: bytes, number: int) -> list[dict]:
    """Row directory of a data page; 0xc000 slots are empty deleted rows."""
    count = u16(image, 8)
    require(10 + 2 * count <= PAGE, f'page {number}: bounded directory')
    rows, previous = [], PAGE
    for ordinal in range(count):
        word = u16(image, 10 + 2 * ordinal)
        if word == 0xc000:
            rows.append({'row': ordinal, 'start': previous, 'end': previous, 'hidden': True, 'overflow': True})
            continue
        start = word & 0x1fff
        require(not word & 0x2000 and 10 + 2 * count <= start <= previous, f'page {number}: directory slot {ordinal}')
        rows.append({'row': ordinal, 'start': start, 'end': previous,
                     'hidden': bool(word & 0x8000), 'overflow': bool(word & 0x4000)})
        previous = start
    return rows


def locator_row(data: bytes, where: dict, what: str) -> bytes:
    image = page(data, where['page'], what)
    require(image[0] == 1, f'{what}: map page {where["page"]} has tag {image[0]}')
    entries = directory(image, where['page'])
    require(where['row'] < len(entries), f'{what}: map page {where["page"]} lacks row {where["row"]}')
    entry = entries[where['row']]
    require(not entry['hidden'] and not entry['overflow'], f'{what}: map row is not active')
    return image[entry['start']:entry['end']]


def _ranges(values):
    result = []
    for value in sorted(values):
        if result and result[-1][1] == value:
            result[-1][1] += 1
        else:
            result.append([value, value + 1])
    return result


def map_record(data: bytes, where: dict, role: str):
    """Inline (type 0) or 33-slot indirect (type 1) allocation map; returns (record, members)."""
    raw = locator_row(data, where, role)
    pages = len(data) // PAGE
    members, references, active = set(), [], []

    def bits(bitmap, base):
        for offset, byte in enumerate(bitmap):
            for bit in range(8):
                if byte & (1 << bit):
                    members.add(base + offset * 8 + bit)

    if raw[0] == 0:
        require(len(raw) >= 5, f'{role}: complete inline map')
        start = u32(raw, 1)
        bits(raw[5:], start)
    else:
        require(raw[0] == 1 and len(raw) == 133, f'{role}: complete 33-slot indirect map')
        start = 0
        references = [u32(raw, offset) for offset in range(1, len(raw), 4)]
        active = [r for r in references if r]
        require(len(active) == len(set(active)) and references[:len(active)] == active,
                f'{role}: distinct active bitmap prefix and zero tail')
        for slot, reference in enumerate(references):
            if not reference:
                continue
            require(0 < reference < pages and reference != where['page'], f'{role}: bounded bitmap reference')
            bitmap = page(data, reference, 'bitmap')
            require(bitmap[:4] == b'\x05\x01\x00\x00', f'{role}: extended bitmap header')
            bits(bitmap[4:], slot * 16352)
    beyond = {p for p in members if p >= pages}
    if role == 'global':
        end = start + (len(raw) - 5) * 8 if raw[0] == 0 else len(active) * 16352
        require(start == 0 and beyond == set(range(pages, max(pages, end))), 'global: represented tail beyond EOF is free')
    require(role == 'global' or not beyond, f'{role}: members are inside the image')
    members -= beyond
    record = {'locator': where, 'kind': raw[0], 'length': len(raw), 'start': start, 'references': references,
              'raw_hex': raw.hex(), 'members': _ranges(members), 'outside_eof': _ranges(beyond)}
    return record, members


def definition(data: bytes, root: int) -> dict:
    """Complete table definition, including deleted-column storage IDs (EXP-0297)."""
    pages, body, current = [], bytearray(), root
    while current:
        require(current not in pages and current < len(data) // PAGE, f'definition {root}: chain')
        image = data[current * PAGE:(current + 1) * PAGE]
        require(image[:4] == DEFINITION_PREFIX, f'definition {root}: prefix')
        body += image if not pages else image[8:]
        pages.append(current)
        current = u32(image, 4)
    total = u32(body, 8)
    require(43 <= total <= len(body), f'definition {root}: length')
    body = bytes(body[:total])
    high, var_high, live, logical_count, physical_count = [u16(body, i) for i in (21, 23, 25, 27, 31)]
    require(live <= high <= 255 and var_high <= high, f'definition {root}: high water')

    def file_offset(position):
        if position < PAGE:
            return root * PAGE + position
        index, within = divmod(position - PAGE, PAGE - 8)
        return pages[index + 1] * PAGE + 8 + within

    offset = 43
    prefixes = [body[offset + 8 * i:offset + 8 * (i + 1)] for i in range(physical_count)]
    offset += 8 * physical_count
    columns = []
    for ordinal in range(live):
        raw = body[offset:offset + 18]
        offset += 18
        require(len(raw) == 18 and raw[0] in PHYSICAL_TYPES and raw[13] & 7 in (2, 3, 7), f'definition {root}: column record')
        storage = 'variable' if raw[13] & 7 == 2 else 'fixed'
        columns.append({'ordinal': ordinal, 'storage_id': u16(raw, 1), 'ordinal_repeat': u16(raw, 5),
                        'type': PHYSICAL_TYPES[raw[0]], 'type_code': raw[0], 'class': raw[13], 'constant': u16(raw, 7),
                        'context_hex': raw[9:13].hex(), 'fixed_offset': u16(raw, 14) if storage == 'fixed' else None,
                        'size': u16(raw, 16), 'variable_index': u16(raw, 3), 'storage': storage, 'raw_hex': raw.hex()})
    require(len({c['storage_id'] for c in columns}) == live and all(c['storage_id'] < high for c in columns),
            f'definition {root}: unique stored IDs')
    variable = [c['variable_index'] for c in columns if c['storage'] == 'variable']
    require(len(set(variable)) == len(variable) and all(v < var_high for v in variable), f'definition {root}: variable high water')

    def take_name():
        nonlocal offset
        length = body[offset]
        value = body[offset + 1:offset + 1 + length].decode('cp1252')
        offset += 1 + length
        require(len(value) == length, f'definition {root}: name bound')
        return value

    for column in columns:
        column['name'] = take_name()
    by_id = {c['storage_id']: c for c in columns}
    physical = []
    for index in range(physical_count):
        raw = body[offset:offset + 39]
        offset += 39
        index_keys = []
        for slot in range(10):
            storage_id = u16(raw, slot * 3)
            if storage_id == 65535:
                continue
            require(slot == len(index_keys) and storage_id in by_id and raw[slot * 3 + 2] in (0, 1),
                    f'definition {root}: physical keys')
            index_keys.append({'column': by_id[storage_id]['ordinal'], 'storage_id': storage_id, 'direction': raw[slot * 3 + 2]})
        require(index_keys, f'definition {root}: nonempty index')
        physical.append({'index': index, 'keys': index_keys, 'flags': raw[38], 'root': u32(raw, 34), 'map': locator(raw, 30),
                         'entry_count': u32(prefixes[index], 4), 'entry_count_offset': file_offset(43 + index * 8 + 4),
                         'prefix_hex': prefixes[index][:4].hex(), 'raw_hex': raw.hex()})
    logical = []
    for _ in range(logical_count):
        raw = body[offset:offset + 20]
        offset += 20
        require(len(raw) == 20 and u32(raw, 4) < physical_count, f'definition {root}: logical physical selection')
        logical.append({'class': raw[19], 'selector': u32(raw, 0), 'physical_index': u32(raw, 4), 'raw_hex': raw.hex()})
    require(len({i['selector'] for i in logical}) == logical_count, f'definition {root}: unique logical selectors')
    for entry in logical:
        entry['name'] = take_name()
    suffix = body[offset:-2]
    require(body[-2:] == b'\xff\xff' and len(suffix) % 10 == 0, f'definition {root}: suffix')
    groups = []
    for start in range(0, len(suffix), 10):
        raw = suffix[start:start + 10]
        storage_id = u16(raw, 0)
        require(storage_id in by_id and by_id[storage_id]['type'] in ('Memo', 'LongBinary'), f'definition {root}: payload map column')
        column = by_id[storage_id]
        groups.append({'column': column['ordinal'], 'storage_id': storage_id, 'column_name': column['name'],
                       'owned': locator(raw, 2), 'available': locator(raw, 6)})
    return {'root': root, 'pages': pages, 'logical_length': total, 'row_count': u32(body, 12),
            'row_count_offset': file_offset(12), 'header_unknown_hex': body[16:20].hex() + body[33:35].hex(),
            'marker': body[20], 'storage_high_water': high, 'variable_high_water': var_high,
            'columns': columns, 'logical_indexes': logical, 'physical_indexes': physical,
            'maps': {'owned': locator(body, 35), 'available': locator(body, 39)},
            'long_value_maps': groups, 'suffix_hex': suffix.hex(), 'body_hex': body.hex()}


def fields(raw: bytes, columns: list[dict], variable_high_water: int) -> list:
    """Raw field bytes by column; deleted variable slots remain in older row formats."""
    count = raw[0]
    presence_length = (count + 7) // 8
    require(count > 0 and len(raw) > presence_length, 'stored row framing')
    presence = raw[-presence_length:]
    trailer = len(raw) - presence_length
    bounds = []
    if variable_high_water:
        count_position = trailer - 1
        variable_count = raw[count_position]
        jumps = (len(raw) - 1) // 256
        end = count_position - jumps - variable_count - 1
        require(end >= 1, 'row trailer')
        lows = list(reversed(raw[end:end + variable_count + 1]))
        jump_values = raw[end + variable_count + 1:count_position]
        bounds = [low + 256 * sum(j != 255 and j <= i for j in jump_values) for i, low in enumerate(lows)]
        if variable_count == 255:
            bounds[-1] = end
        require(bounds[-1] == end and all(a <= b for a, b in zip(bounds, bounds[1:])), 'row offsets')
        fixed_end = bounds[0]
    else:
        fixed_end = trailer
    values = []
    for column in columns:
        stored = column['storage_id']
        present = stored < count and bool(presence[stored // 8] & (1 << (stored % 8)))
        if column['type'] == 'Boolean':
            values.append(bool(present))
            continue
        if not present:
            values.append(None)
            continue
        if column['storage'] == 'fixed':
            start = 1 + column['fixed_offset']
            end = start + column['size']
            require(end <= fixed_end, 'present fixed field bound')
            values.append(raw[start:end])
        else:
            slot = column['variable_index']
            require(slot + 1 < len(bounds), 'present variable slot')
            values.append(raw[bounds[slot]:bounds[slot + 1]])
    return values


def decode_value(column: dict, field: bytes):
    """Scalar value of present field bytes; unmodeled types keep their raw bytes."""
    kind = column['type']
    if kind in ('Long', 'Integer', 'Byte'):
        width = {'Long': 4, 'Integer': 2, 'Byte': 1}[kind]
        if len(field) == width:
            return int.from_bytes(field, 'little', signed=kind != 'Byte')
    elif kind == 'Date' and len(field) == 8:
        number = struct.unpack('<d', field)[0]
        if number == number and abs(number) != float('inf'):
            return number
    elif kind == 'Text':
        try:
            return field.decode('cp1252')
        except UnicodeDecodeError:
            pass
    elif kind == 'Binary':
        return field.hex()
    return {'raw_hex': field.hex()}


def payload(data: bytes, field: bytes, owned: set, reached: set, chain: list | None = None) -> bytes:
    """Complete Memo/OLE value: inline, single-page or chained (EXP-0077/0234).

    `chain`, when given, receives (page, row, fragment length) for each external fragment.
    """
    require(len(field) >= 12, 'payload descriptor')
    word = u32(field, 0)
    length, flags = word & 0xffffff, word & 0xff000000
    require(field[8:12] == bytes(4), 'payload reserved bytes')
    if flags == 0x80000000:
        require(field[4:8] == bytes(4) and len(field) == 12 + length, 'inline payload')
        return field[12:]
    require(flags in (0, 0x40000000) and len(field) == 12, 'external payload')
    where, value = locator(field, 4), bytearray()
    while where['page']:
        key = (where['page'], where['row'])
        require(where['page'] in owned and key not in reached, 'distinct owned payload')
        reached.add(key)
        image = page(data, where['page'], 'payload')
        require(image[:2] == b'\x01\x01' and image[4:8] == LONG_VALUE_OWNER, 'payload page')
        entry = directory(image, where['page'])[where['row']]
        require(not entry['hidden'] and not entry['overflow'], 'live payload fragment')
        fragment = image[entry['start']:entry['end']]
        if chain is not None:
            chain.append((where['page'], where['row'], len(fragment)))
        if flags == 0x40000000:
            value += fragment
            break
        require(len(fragment) > 4, 'payload fragment framing')
        value += fragment[4:]
        require(len(value) <= length, 'payload length bound')
        where = locator(fragment, 0)
    require(len(value) == length, 'complete payload length')
    return bytes(value)


def _owned_pages(data, table_definition, what):
    """Owned pages of a table: its data pages and LVAL pages (EXP-0069)."""
    data_pages, long_value_pages = [], []
    _, members = map_record(data, table_definition['maps']['owned'], what)
    for number in sorted(members):
        image = page(data, number, what)
        require(image[0] == 1, f'{what}: owned page {number} has tag {image[0]}')
        if image[4:8] == LONG_VALUE_OWNER:
            long_value_pages.append(number)
        else:
            require(u32(image, 4) == table_definition['root'], f'{what}: owned page {number} has another owner')
            data_pages.append(number)
    return data_pages, long_value_pages


def rows(data: bytes, table: dict) -> list[dict]:
    """Every live row with logical locator, storage target, decoded values and payloads."""
    d = table['definition']
    owned = {g['column']: map_record(data, g['owned'], 'payload owned')[1] for g in d['long_value_maps']}
    reached = {ordinal: set() for ordinal in owned}
    result, physical, targets = [], set(), set()
    for number in table['data_pages']:
        image = page(data, number, 'data')
        for entry in directory(image, number):
            if entry['start'] < entry['end'] and not entry['overflow']:
                physical.add((number, entry['row']))
            if entry['hidden']:
                continue
            origin = (number, entry['row'])
            current, current_image, seen = number, image, set()
            while True:
                require((current, entry['row']) not in seen, 'row cycle')
                seen.add((current, entry['row']))
                raw = current_image[entry['start']:entry['end']]
                if not entry['overflow']:
                    break
                require(len(raw) == 4, 'row link')
                target = locator(raw, 0)
                current = target['page']
                require(current in table['data_pages'], 'row link owner')
                current_image = page(data, current, 'row link')
                entry = directory(current_image, current)[target['row']]
                require(entry['hidden'], 'hidden link target')
            storage = (current, entry['row'])
            require(storage not in targets, 'unique row storage')
            targets.add(storage)
            decoded, descriptors = {}, {}
            for column, value in zip(d['columns'], fields(raw, d['columns'], d['variable_high_water'])):
                if column['type'] == 'Boolean' or value is None:
                    decoded[column['name']] = value
                elif column['type'] in ('Memo', 'LongBinary'):
                    require(column['ordinal'] in owned, 'payload map exists')
                    decoded[column['name']] = payload(data, value, owned[column['ordinal']], reached[column['ordinal']]).hex()
                    descriptors[column['name']] = value.hex()
                else:
                    decoded[column['name']] = decode_value(column, value)
            result.append({'locator': {'page': origin[0], 'row': origin[1]}, 'storage': {'page': storage[0], 'row': storage[1]},
                           'values': decoded, 'descriptors': descriptors, 'raw_hex': raw.hex()})
    require(targets == physical, 'all stored rows reachable')
    for ordinal, members in owned.items():
        active = set()
        for number in members:
            image = page(data, number, 'owned payload')
            require(image[:2] == b'\x01\x01' and image[4:8] == LONG_VALUE_OWNER, 'owned payload page')
            active.update((number, e['row']) for e in directory(image, number) if not e['hidden'])
        require(active == reached[ordinal], f'complete payload reachability {table["name"]}/{ordinal}')
    return result


def tables(data: bytes) -> dict[str, dict]:
    """Tables named by the MSysObjects catalog (EXP-0066/0072), keyed by name."""
    require(len(data) % PAGE == 0 and len(data) >= 2 * PAGE and data[0] == 0 and data[PAGE] == 1,
            'header and global-map pages')
    found = []
    for number in range(2, len(data) // PAGE):
        if data[number * PAGE] != 2 or data[number * PAGE:number * PAGE + 4] != DEFINITION_PREFIX:
            continue
        try:
            candidate = definition(data, number)
            if not any(c['name'] == 'Name' for c in candidate['columns']):
                continue
            data_pages, _ = _owned_pages(data, candidate, 'catalog')
            decoded = rows(data, {'name': 'catalog', 'definition': candidate, 'data_pages': data_pages})
        except (DecodeError, IndexError, UnicodeDecodeError):
            continue
        if any(r['values'].get('Name') == 'MSysObjects' for r in decoded):
            found.append(decoded)
    require(len(found) == 1, 'exactly one MSysObjects catalog')
    result = {}
    for row in found[0]:
        values = row['values']
        if values.get('Type') != 1:
            continue
        root, name = values['Id'], values['Name']
        require(isinstance(root, int) and 2 <= root < len(data) // PAGE and data[root * PAGE] == 2,
                f'catalog table {name!r} names definition page {root!r}')
        require(name not in result, f'catalog names table {name!r} twice')
        table = definition(data, root)
        data_pages, long_value_pages = _owned_pages(data, table, f'table {name}')
        result[name] = {'name': name, 'root': root, 'flags': values['Flags'], 'definition': table,
                        'data_pages': data_pages, 'long_value_pages': long_value_pages}
    return result


def _record_width(record, fields_spec):
    offset = 0
    for kind, descending in fields_spec:
        require(offset < len(record), 'missing key component')
        marker = record[offset] ^ (255 if descending else 0)
        require(marker in (0, 127) and not (kind == 1 and marker == 0), 'key component marker')
        offset += 1
        if not marker:
            continue
        mask = 255 if descending else 0
        if kind == 10:
            while offset < len(record) and (record[offset] ^ mask) >= 16:
                offset += 1
            require(offset < len(record) and record[offset] ^ mask < 16, 'Text secondary prefix')
            nibble = offset * 2 + 1
            while nibble < len(record) * 2:
                byte = record[nibble // 2] ^ mask
                value = byte >> 4 if nibble % 2 == 0 else byte & 15
                if value == 0:
                    require(nibble % 2 or byte & 15 == 0, 'Text secondary padding')
                    offset = nibble // 2 + 1
                    break
                require(2 <= value <= 10, 'Text secondary nibble')
                nibble += 1
            else:
                raise DecodeError('unterminated Text component')
        elif kind == 15:
            require(offset + 18 <= len(record) and record[offset + 8] == 9 and record[offset + 17] == (8 ^ mask),
                    'GUID chunk framing')
            offset += 18
        elif kind == 9:
            for chunk in range(32):
                require(offset + 9 <= len(record), 'incomplete Binary chunk')
                suffix, body = record[offset + 8], record[offset:offset + 8]
                offset += 9
                if suffix == 9:
                    continue
                width = suffix ^ mask
                require(1 <= width <= 8 and chunk * 8 + width <= 255, 'Binary terminal length')
                require(body[width:] == bytes([mask]) * (8 - width), 'Binary padding')
                break
            else:
                raise DecodeError('unterminated Binary component')
        else:
            offset += keys.SIZES[kind]
    return offset + 4


def tree(data: bytes, root: int, owner: int, fields_spec: list) -> tuple[list[dict], list[bytes]]:
    """Complete index tree (EXP-0062, fences EXP-0225, one-child root EXP-0246).

    `fields_spec` lists (type code, ascending) per key column. Returns the nodes and every
    leaf entry (key and row locator) in traversal order.
    """
    nodes, seen, leaf_entries = [], set(), []

    def visit(number, depth):
        require(depth <= 32 and number not in seen, 'repeated page or excessive index depth')
        seen.add(number)
        image = page(data, number, 'index node')
        branch = image[0] == 3
        require(image[0] in (3, 4) and image[1] == 1 and image[21] in ((1, 2, 3) if branch else (0,)), 'index node header')
        require(u32(image, 4) == owner, 'index owner')
        prefix = image[20]
        previous, following, tail = u32(image, 8), u32(image, 12), u32(image, 16)
        require(bool(tail) == branch, 'index tail child')
        area = image[248:]
        ends = [position * 8 + bit for position, byte in enumerate(image[22:248]) for bit in range(8) if byte & (1 << bit)]
        require(all(prefix < end <= 1800 for end in ends), 'index boundary outside the entry area')
        require(u16(image, 2) == 1800 - (ends[-1] if ends else 0), 'index free space')
        require(bool(ends) or prefix == 0, 'empty node with a prefix')
        require(bool(ends) or not branch or (depth == 1 and image[21] == 1), 'unobserved empty intermediate node')
        node = {'page': number, 'depth': depth, 'previous': previous, 'next': following, 'tail': tail, 'prefix': prefix,
                'entries': len(ends), 'children': [], 'header_class': image[21], 'stale_separators': 0}
        nodes.append(node)
        start, entries = prefix, []
        for end in ends:
            entry = area[:prefix] + area[start:end]
            shortened = any(kind in (9, 10) for kind, _ in fields_spec) and len(entry) == 259 + (4 if branch else 0)
            require(shortened or len(entry) == _record_width(entry, fields_spec) + (4 if branch else 0), 'index record width')
            entries.append(entry)
            start = end
        require(entries == sorted(entries), 'unsorted index entries')
        if not branch:
            leaf_entries.extend(entries)
            node['subtree_height'] = 0
            return (entries[0], entries[-1], 0) if entries else (None, None, 0)
        children = []
        for entry in entries:
            child = int.from_bytes(entry[-4:], 'big')
            node['children'].append(child)
            children.append(visit(child, depth + 1))
        node['children'].append(tail)
        children.append(visit(tail, depth + 1))
        require(all(low is not None and high is not None for low, high, _ in children), 'empty index child')
        require(len({height for _, _, height in children}) == 1, 'unequal child subtree heights')
        for position, entry in enumerate(entries):
            maximum, next_minimum = children[position][1], children[position + 1][0]
            require(maximum <= entry[:-4] < next_minimum, 'branch separator bounds')
            node['stale_separators'] += int(maximum != entry[:-4])
        node['subtree_height'] = children[0][2] + 1
        return children[0][0], children[-1][1], node['subtree_height']

    visit(root, 1)
    for depth in sorted({node['depth'] for node in nodes}):
        level = [node for node in nodes if node['depth'] == depth]
        for position, node in enumerate(level):
            require(node['previous'] == (level[position - 1]['page'] if position else 0)
                    and node['next'] == (level[position + 1]['page'] if position + 1 < len(level) else 0), 'index sibling chain')
    require(len({node['depth'] for node in nodes if not node['children']}) == 1, 'unequal leaf depth')
    require(leaf_entries == sorted(leaf_entries), 'index traversal is not sorted')
    return nodes, leaf_entries


def index_key(value, column: dict, descending: bool, text=keys.general_text) -> bytes:
    """Directed key component of one decoded row value."""
    kind = column['type_code']
    if kind == 10 and value is not None:
        value = bytes.fromhex(value['raw_hex']) if isinstance(value, dict) else value.encode('cp1252')
    elif isinstance(value, dict):
        require(kind in (3, 4, 5), 'unmodeled index type')
        value = int.from_bytes(bytes.fromhex(value['raw_hex']), 'little', signed=True)
    return keys.component(value, kind, descending, text)


def general_text_model(context_hex):
    return keys.general_text


def observe(path: Path, count_deltas: dict | None = None, text_model=general_text_model) -> dict:
    """Complete raw observation: tables, rows, index trees against rebuilt keys, every map,
    and a complete page classification. `count_deltas` allows a declared row count to
    exceed the live rows (native refusal residue); `text_model(context_hex)` selects the
    Text key model for a column's collation context.
    """
    data = Path(path).read_bytes()
    require(len(data) % PAGE == 0, 'complete pages')
    named = tables(data)
    result_tables, maps = {}, {}

    def add_map(role, where):
        record, members = map_record(data, where, role)
        maps[role] = {'record': record, 'members': sorted(members)}
        return set(members)

    free = add_map('global', {'page': 1, 'row': 0})
    metadata, claimed = {0, 1}, {}
    for name, table in named.items():
        d = table['definition']
        metadata.update(d['pages'])
        decoded = rows(data, table)
        require(d['row_count'] - len(decoded) == (count_deltas or {}).get(name, 0), f'declared/live table count {name}')
        groups = [('table', d['maps']['owned'], d['maps']['available'])]
        groups += [(f'index/{i["index"]}', i['map'], None) for i in d['physical_indexes']]
        groups += [(f'lval/{g["column_name"]}', g['owned'], g['available']) for g in d['long_value_maps']]
        for role, owned, available in groups:
            members = add_map(f'{name}/{role}/owned', owned)
            require(not members & free, 'owned page is globally free')
            for number in members:
                require(number not in claimed, 'unique page ownership')
                claimed[number] = f'{name}/{role}'
            if available:
                require(add_map(f'{name}/{role}/available', available) <= members, 'availability subset')
        physical = []
        for index in d['physical_indexes']:
            columns = [d['columns'][k['column']] for k in index['keys']]
            spec = [(c['type_code'], k['direction'] == 0) for c, k in zip(columns, index['keys'])]
            nodes, entries = tree(data, index['root'], d['root'], spec)
            expected = []
            for row in decoded:
                if index['flags'] & 2 and any(row['values'][c['name']] is None for c in columns):
                    continue
                key = b''.join(index_key(row['values'][c['name']], c, k['direction'] == 0, text_model(c['context_hex']))
                               for c, k in zip(columns, index['keys']))
                expected.append(keys.shorten(key) + row['locator']['page'].to_bytes(3, 'big') + bytes([row['locator']['row']]))
            require(entries == sorted(expected), f'{Path(path).name}/{name}/{index["index"]} complete physical keys')
            members = set(maps[f'{name}/index/{index["index"]}/owned']['members'])
            require({n['page'] for n in nodes} <= members, 'all tree pages owned')
            for number in members:
                image = page(data, number, 'owned index')
                require(image[0] in (3, 4) and u32(image, 4) == d['root'], 'owned index page owner')
            physical.append(dict(index, nodes=nodes, entries_hex=[e.hex() for e in entries],
                                 live_distinct=len({e[:-4] for e in entries})))
        result_tables[name] = {'definition': d, 'rows': decoded, 'indexes': physical}
    locators, references = [], []
    for record in maps.values():
        where = record['record']['locator']
        locators.append((where['page'], where['row']))
        metadata.add(where['page'])
        for number in record['record']['references']:
            if number:
                references.append(number)
                metadata.add(number)
    require(len(locators) == len(set(locators)) and len(references) == len(set(references)), 'unique maps and bitmaps')
    active = set()
    for number in {p for p, _ in locators}:
        for entry in directory(page(data, number, 'map container'), number):
            if not entry['hidden'] and not entry['overflow']:
                active.add((number, entry['row']))
    require(active - set(locators) == {(1, 1)}, 'only the native empty global reserve map is unreferenced')
    require(locator_row(data, {'page': 1, 'row': 1}, 'global reserve') == bytes(133), 'native empty global reserve bytes')
    require(not metadata & free and not metadata & set(claimed), 'metadata allocation separation')
    require(free | metadata | set(claimed) == set(range(len(data) // PAGE)), 'complete page allocation classification')
    return {'identity': {'size': len(data), 'sha256': sha(data)}, 'page0_hex': data[:PAGE].hex(), 'tables': result_tables,
            'maps': maps, 'free_pages': sorted(free), 'metadata_pages': sorted(metadata),
            'owned_pages': {str(p): role for p, role in sorted(claimed.items())}}
