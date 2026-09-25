"""Wide rows (EXP-0257/0258/0259): 2 to 254 variable columns, 260- and 767-byte fixed Text
areas and a mixed Memo/OLE table, with canonical jump bytes, overflow rows and complete
payload and map checks; see `registry.scalar`. Three malformed rows must be refused.
"""

from __future__ import annotations

import json

import recipes
import structure
from registry import common, scalar
from registry.common import require

SCRIPT = common.REGISTRY / 'wide_rows.ps1'
CONFIG = {'vars2': (2, 5, False), 'vars3': (3, 5, False), 'vars8': (8, 5, False), 'vars32': (32, 5, False),
          'vars254': (254, 5, False), 'fixed260': (2, 260, False), 'fixed767': (2, 767, False), 'mixed': (4, 5, True)}
ALPHABET = b'aAezZ\xe9\xc9\xc6\xe6\xdf\x8a\x9a'
MALFORMED = 'row stream failed'


def payload(length, id, column, text):
    return bytes(ALPHABET[(n * 7 + id * 13 + column * 31) % len(ALPHABET)] if text else (n * 37 + id * 13 + column * 31) % 256
                 for n in range(length)).hex()


def maximum(name):
    return {2: 510, 3: 765, 8: 1988, 32: 1600, 254: 1400}.get(CONFIG[name][0], 510)


def row(name, id, total=None, sparse=False):
    variables, fixed, mixed = CONFIG[name]
    count = (fixed - 5 + 254) // 255
    values = [id]
    for i in range(count):
        values.append(payload(min(fixed - 5 - 255 * i, 255), id, i + 1, True))
    actual = 2 if mixed else variables
    total = maximum(name) if total is None else total
    for i in range(actual):
        length = total // actual + int(i < total % actual)
        values.append(None if sparse and i + 1 != actual else payload(1 if sparse else length, id, i + count + 1, mixed or i % 2 == 0))
    if mixed:
        values.extend([payload(80, id, 30, True), payload(80, id, 31, False)])
    return values


def layout(raw, columns):
    """Canonical row framing (EXP-0257/0258): exact fixed area, bounds and descending jumps."""
    count = raw[0]
    require(count == len(columns), 'Current candidate physical column count')
    presence_length = (count + 7) // 8
    presence = raw[-presence_length:]
    variables = sum(c['storage'] == 'variable' for c in columns)
    fixed = 1 + max((c['fixed_offset'] + c['size'] for c in columns if c['storage'] == 'fixed'), default=0)
    jumps, boundaries = b'', []
    if variables:
        position = len(raw) - presence_length - 1
        require(raw[position] == variables, 'Exact stored variable count')
        jump_count = (len(raw) - 1) // 256
        end = position - jump_count - variables - 1
        require(fixed <= end and (jump_count == 0 or len(raw) - 1 > 256 * jump_count), 'Minimal jump framing')
        lows = list(reversed(raw[end:end + variables + 1]))
        jumps = raw[end + variables + 1:position]
        boundaries = [low + 256 * sum(j != 255 and j <= i for j in jumps) for i, low in enumerate(lows)]
        if variables == 255:
            boundaries[-1] = end
        require(boundaries[0] == fixed and boundaries[-1] == end and lows[-1] == end % 256
                and all(a <= b for a, b in zip(boundaries, boundaries[1:])), 'Exact ordered full row boundaries')
        expected = [next((i for i, b in enumerate(boundaries) if b >= 256 * k), 255) for k in range(jump_count, 0, -1)]
        require(list(jumps) == expected, 'Every canonical descending threshold jump')
    else:
        end = len(raw) - presence_length
        require(end == fixed, 'Exact fixed-only body')
    for c in columns:
        ordinal = c['ordinal']
        if c['storage'] == 'variable' and not presence[ordinal // 8] & 1 << (ordinal % 8):
            i = c['variable_index']
            require(boundaries[i] == boundaries[i + 1], 'Null variable occupies no bytes')
    return dict(length=len(raw), columns=count, variables=variables, fixed=fixed, data_end=end,
                boundaries=boundaries, jumps=list(jumps), presence=presence.hex())


def table_rows(data, table):
    """Rows through overflow links with canonical layouts, bounded payloads and LVAL tombstones."""
    rows = common.table_rows(data, table, direct=False, wide=True)
    for r in rows:
        r['layout'] = layout(bytes.fromhex(r['raw_hex']), table['columns'])
        for descriptor in r['descriptors'].values():
            require(int.from_bytes(bytes.fromhex(descriptor)[:3], 'little') <= 4096, 'Finite payload header')
    for group in table['long_value_maps']:
        for page in common.map_pages(data, group['owned']):
            for entry in structure.directory(common.page_bytes(data, page), page):
                require(not entry['hidden'] or (entry['overflow'] and entry['start'] == entry['end']), 'Empty payload tombstone')
    return rows


def damage(data, name):
    """(offset, value) of one malformed framing byte in vars3 row 0: the first jump ordinal,
    the low byte of the data end, or the variable count (EXP-0257/0258)."""
    where = recipes.locate(data, 'Items', 0)
    base, slot = where['page'] * common.PAGE, where['slot']

    def word(offset):
        return int.from_bytes(data[offset:offset + 2], 'little') & 0x1fff

    start = base + word(base + 10 + 2 * slot)
    end = base + (common.PAGE if slot == 0 else word(base + 8 + 2 * slot))
    count = end - (data[start] + 7) // 8 - 1
    jump = count - (end - start - 1) // 256
    low = jump - CONFIG['vars3'][0] - 1
    return {'jump-ordinal': (jump, 254), 'end-low': (low, data[low] ^ 1), 'variable-count': (count, 255)}[name]


def damaged(data, name):
    offset, value = damage(data, name)
    result = bytearray(data)
    result[offset] = value
    return bytes(result)


def maps(data):
    """Every allocation map record, with disjoint ownership and separate metadata."""
    definitions = common.tables(data)
    locations = {'global': dict(page=1, row=0)}
    owners, available = [], []
    for name, table in definitions.items():
        for role, where in table['maps'].items():
            locations[f'{name}-{role}'] = where
        owners.append(f'{name}-owned')
        available.append((f'{name}-available', f'{name}-owned'))
        for physical in table['physical_indexes']:
            key = f"{name}-index{physical['index']}"
            locations[key] = physical['map']
            owners.append(key)
        for group in table['long_value_maps']:
            for role in ('owned', 'available'):
                locations[f"{name}-lval{group['column']}-{role}"] = group[role]
            key = f"{name}-lval{group['column']}"
            owners.append(key + '-owned')
            available.append((key + '-available', key + '-owned'))
    require(len({(v['page'], v['row']) for v in locations.values()}) == len(locations), 'Distinct map rows')
    records, members, bitmaps, owned = {}, {}, set(), set()
    for role, where in locations.items():
        records[role], members[role] = structure.map_record(data, where, role)
        references = {p for p in records[role]['references'] if p}
        require(not references & bitmaps, 'Distinct role bitmap pages')
        bitmaps |= references
    for role in owners:
        require(not members[role] & (owned | members['global']), 'Independent globally allocated ownership')
        owned |= members[role]
    for a, o in available:
        require(members[a] <= members[o], 'Availability subset')
    metadata = bitmaps | {v['page'] for v in locations.values()} | {p for t in definitions.values() for p in t['pages']}
    require(not metadata & (owned | members['global']), 'Allocated metadata is separate from data')
    return records


class Wide(scalar.Scalar):
    MANIFEST = 'wide-row-lifecycle.json'
    CASE_NAMES = tuple(CONFIG)
    SUMMARY = 'wide-row'
    REFUSAL_SOURCE = 'vars3-original'
    REFUSALS = ('jump-ordinal', 'end-low', 'variable-count')

    def initial_row(self, name, id):
        return row(name, id)

    def recipe(self, name):
        variables, fixed, mixed = CONFIG[name]
        count = (fixed - 5 + 254) // 255
        fields = [['Id', 4, 4]] + [[f'F{i:03}', 10, min(fixed - 5 - 255 * i, 255)] for i in range(count)]
        fields += [[f'V{i:03}', 12 if mixed and i == 2 else 11 if mixed and i == 3 else 10 if mixed or i % 2 == 0 else 9,
                    0 if mixed and i >= 2 else 255] for i in range(variables)]
        indexes = [dict(name='ById', fields=[[0, False]], primary=True, unique=True, required=True, ignore=False)]
        if variables <= 8:
            indexes.append(dict(name='ByText', fields=[[count + 1, True]], primary=False, unique=False, required=False, ignore=False))

        def replace(id, value):
            return dict(kind='replace', id=id, row=value)

        def insert(id, value=None):
            return dict(kind='insert', row=row(name, id) if value is None else value)

        operations = [replace(0, row(name, 0))]
        operations += [insert(id, row(name, id, max(variables, min(maximum(name), width)))) for id, width in zip(range(1000, 1004), (240, 249, 495, 751))]
        operations += [dict(kind='delete', id=1), dict(kind='field', id=7, column=0, value=99), dict(kind='delete', id=2)]
        for index in indexes:
            values = [row(name, id) for id in (0, 8, 9000, 1234567, 12345)]
            index['queries'] = list({json.dumps([r[c] for c, _ in index['fields']]): [r[c] for c, _ in index['fields']] for r in values}.values())
        return dict(name=name, fields=fields, fixed_fields=[f'F{i:03}' for i in range(count)], indexes=indexes,
                    initial_rows=[row(name, id) for id in range(16)],
                    stages=[dict(name='original', operations=[]), dict(name='sparse', operations=[replace(0, row(name, 0, 1, True))]),
                            dict(name='regrown', operations=operations)],
                    native=[insert(9000), dict(kind='field', id=9000, column=0, value=9001), dict(kind='delete', id=1000)])

    def raw_rows(self, data, table, case):
        rows = table_rows(data, table)
        for r in rows:
            r['values'] = [value.encode('cp1252').hex() if value is not None and case['fields'][n][1] == 10 else value
                           for n, value in enumerate(r['values'])]
        return rows

    def raw_extra(self, data, case, table, result):
        for column in table['columns']:
            require((column['storage'] == 'fixed') == (column['name'] == 'Id' or column['name'] in case['fixed_fields']), 'Exact fixed/variable schema storage')
        result['rows'] = [dict(id=r['values'][0], **r['layout']) for r in sorted(table_rows(data, table), key=lambda r: r['values'][0])]
        result['maps'] = maps(data)

    def normalized(self, capture, case, rows):
        result = super().normalized(capture, case, rows)
        for table in result['user_tables']:
            for f in table['fields']:
                fixed = f['type'] == 4 or (table['name'] == 'Items' and f['name'] in case['fixed_fields'])
                require(f == dict(name=f['name'], type=f['type'], size=f['size'], attributes=1 if fixed else 2,
                                  required=False, allow_zero_length=False, default_value=''), 'Complete default DAO field properties')
        return result

    def refusal_images(self, case, source):
        """Replacing row 0 after one byte of its row framing is damaged."""
        images = []
        for name in self.REFUSALS:
            step = scalar.write(case, row('vars3', 0), 0, refused=MALFORMED)
            step['locate']['image'] = source
            images += self.refusal_pair(name, source, step, lambda data, name=name: damaged(data, name))
        return images

    def refused_input(self, source, receipt, before):
        receipt['offset'], receipt['value'] = damage(source, receipt['name'])
        require(MALFORMED in receipt['error'], 'Malformed row refusal: ' + receipt['name'])
        try:
            table_rows(before, common.tables(before, ['Items'])['Items'])
        except ValueError:
            pass
        else:
            raise ValueError('Independent raw decoder admitted malformed row')
        return damaged(source, receipt['name'])


scalar.bind(globals(), Wide())
