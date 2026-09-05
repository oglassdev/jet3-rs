"""Finite index-tree/map/row decoding from EXP-0062, EXP-0057/0065 and EXP-0126.

Reuses an isolated system-catalog decoder with explicit experiment limits.
No consumed decoder file or other experiment's runtime instance is modified.
"""
import hashlib
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('multi_level_catalog', Path(__file__).with_name('system_catalog.py'))
catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog)
catalog.MAX_PAGES = 8192
catalog.MAX_ROWS_PER_PAGE = 1019  # complete 2048-byte page directory, not the old sample's 64 slots


def require(condition, detail):
    if not condition:
        raise catalog.DecodeError(detail)


def map_pages(data, locator, what):
    raw = catalog._locator_row(data, locator, what)
    if raw[0] == 0:
        return catalog._map_pages(raw, len(data) // 2048, what, bounded=True)
    require(raw[0] == 1 and (len(raw) - 1) % 4 == 0, 'Invalid indirect map')
    pages = set()
    for slot, offset in enumerate(range(1, len(raw), 4)):
        reference = int.from_bytes(raw[offset:offset + 4], 'little')
        if not reference:
            continue
        bitmap = catalog._page(data, reference, what)
        require(bitmap[:4] == b'\x05\x01\x00\x00', 'Invalid extended bitmap header')
        start = slot * 2044 * 8
        for position, byte in enumerate(bitmap[4:]):
            for bit in range(8):
                if byte & (1 << bit):
                    page = start + position * 8 + bit
                    require(page < len(data) // 2048, 'Map member outside image')
                    pages.add(page)
    return pages


catalog._locator_pages = map_pages


def key_bytes(row, fields, columns):
    key = b''
    for field in fields:
        value = row[columns.index(field['name'])]
        component = b'\x7f' + (value + (1 << 31)).to_bytes(4, 'big')
        key += bytes(byte ^ 255 for byte in component) if field['descending'] else component
    return key


def tree(data, root, owner):
    nodes, seen, leaf_entries = [], set(), []
    def visit(number, depth):
        require(depth <= 32 and number not in seen, 'Repeated page or excessive index depth')
        seen.add(number)
        page = catalog._page(data, number, 'index node')
        branch = page[0] == 3
        require(page[0] in (3, 4) and page[1] == 1 and page[21] == int(branch), 'Index node header')
        require(int.from_bytes(page[4:8], 'little') == owner, 'Index owner mismatch')
        prefix = page[20]
        previous, following, tail = [int.from_bytes(page[offset:offset + 4], 'little') for offset in (8, 12, 16)]
        require(bool(tail) == branch, 'Index tail child mismatch')
        area = page[248:]
        boundaries = [position * 8 + bit for position, byte in enumerate(page[22:248]) for bit in range(8) if byte & (1 << bit)]
        require(all(prefix < end <= 1800 for end in boundaries), 'Index boundary outside entry area')
        require(int.from_bytes(page[2:4], 'little') == 1800 - (boundaries[-1] if boundaries else 0), 'Index free space mismatch')
        require(bool(boundaries) or (not branch and prefix == 0), 'Empty branch or unmatched prefix')
        node = dict(page=number, depth=depth, previous=previous, next=following, tail=tail, prefix=prefix, entries=len(boundaries), children=[])
        nodes.append(node)
        start, entries = prefix, []
        for end in boundaries:
            entry = area[:prefix] + area[start:end]
            require(len(entry) >= (8 if branch else 4), 'Short index entry')
            entries.append(entry)
            start = end
        require(entries == sorted(entries), 'Unsorted complete index entries')
        if not branch:
            leaf_entries.extend(entries)
            return entries[-1] if entries else None
        for entry in entries:
            child = int.from_bytes(entry[-4:], 'big')
            node['children'].append(child)
            maximum = visit(child, depth + 1)
            require(maximum == entry[:-4], 'Branch separator is not child maximum key and locator')
        node['children'].append(tail)
        return visit(tail, depth + 1)
    visit(root, 1)
    for depth in sorted({node['depth'] for node in nodes}):
        level = [node for node in nodes if node['depth'] == depth]
        for position, node in enumerate(level):
            require(node['previous'] == (level[position - 1]['page'] if position else 0)
                    and node['next'] == (level[position + 1]['page'] if position + 1 < len(level) else 0), 'Index sibling chain mismatch')
    require(len({node['depth'] for node in nodes if not node['children']}) == 1, 'Unequal leaf depth')
    require(leaf_entries == sorted(leaf_entries), 'Index traversal is not sorted')
    return nodes, leaf_entries


def observe(data, tables, expected_rows, candidate):
    require(len(data) % 2048 == 0 and 20 <= len(data) // 2048 <= 8192, 'Image page bound')
    definition, _, objects = catalog._discover_catalog(data)
    name, kind, ident = [catalog._ordinal(definition, key) for key in ('Name', 'Type', 'Id')]
    roots = {row['values'][name]: row['values'][ident] for row in objects if row['values'][kind] == 1}
    observations = []
    for table in tables:
        definition = catalog._definition(data, roots[table['name']])
        require([(column['name'], column['type'], column['size']) for column in definition['columns']]
                == [(field['name'], 'Long', 4) for field in table['fields']], 'Physical schema mismatch')
        pages, long_values = catalog._table_pages(data, definition)
        rows = catalog._table_rows(data, definition, pages)
        require(not long_values and sorted(row['values'] for row in rows) == sorted(expected_rows[table['name']])
                and definition['row_count'] == len(rows), 'Physical row mismatch')
        available = map_pages(data, definition['maps']['available'], 'available rows')
        require(available <= set(pages), 'Available pages outside owned data pages')
        require(len(definition['physical_indexes']) == len(table['indexes']), 'Physical index inventory mismatch')
        result = dict(name=table['name'], root=definition['root'], row_count=len(rows), data_pages=pages, available_pages=sorted(available), indexes=[])
        for physical, index in zip(definition['physical_indexes'], table['indexes']):
            nodes, entries = tree(data, physical['root'], definition['root'])
            mapped = map_pages(data, physical['map'], 'index map')
            graph = {node['page'] for node in nodes}
            require(graph <= mapped and not mapped.intersection(pages), 'Index map membership mismatch')
            require(not candidate or graph == mapped, 'Candidate index map has unused members')
            columns = [field['name'] for field in table['fields']]
            by_locator = {(row['page'], row['row']): row['values'] for row in rows}
            seen, keys = set(), []
            for entry in entries:
                locator = (int.from_bytes(entry[-4:-1], 'big'), entry[-1])
                require(locator in by_locator and locator not in seen, 'Missing or repeated row locator')
                seen.add(locator)
                expected = key_bytes(by_locator[locator], index['fields'], columns)
                require(entry[:-4] == expected, 'Index key and row values disagree')
                keys.append(expected)
            require(seen == set(by_locator) and physical['entry_count'] == len(set(keys)), 'Index coverage or distinct count mismatch')
            depth = max(node['depth'] for node in nodes)
            require(not candidate or depth == table['candidate_depth'], 'Candidate did not reach planned depth')
            result['indexes'].append(dict(root=physical['root'], depth=depth, nodes=nodes, mapped_pages=sorted(mapped),
                leaf_entries=len(entries), distinct_keys=physical['entry_count'], physical_flags=physical['flags'],
                locator_key_sha256=hashlib.sha256(b''.join(entries)).hexdigest()))
        observations.append(result)
    return observations
