"""Nullable Long graph/row/map binding from EXP-0148, EXP-0146 and EXP-0062."""
import hashlib
import json
from multi_level_index_structure import catalog, map_pages, require
from multi_level_index_reanalysis import tree


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def key_bytes(row, fields, columns):
    key = b''
    for field in fields:
        value = row[columns.index(field['name'])]
        component = b'\x00' if value is None else b'\x7f' + (value + (1 << 31)).to_bytes(4, 'big')
        key += bytes(byte ^ 255 for byte in component) if field['descending'] else component
    return key


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
        require([(column['class'] & 7) == 7 for column in definition['columns']]
                == [field['auto_increment'] for field in table['fields']], 'Physical AutoIncrement metadata mismatch')
        pages, long_values = catalog._table_pages(data, definition)
        rows = catalog._table_rows(data, definition, pages)
        require(not long_values and sorted((row['values'] for row in rows), key=canonical) == sorted(expected_rows[table['name']], key=canonical)
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
            wanted = {locator for locator, row in by_locator.items() if not index['ignore_nulls'] or any(row[columns.index(field['name'])] is not None for field in index['fields'])}
            require(seen == wanted and physical['entry_count'] == len(set(keys)), 'Index coverage or distinct count mismatch')
            flags = int(index['unique']) | (2 if index['ignore_nulls'] else 0) | (8 if index['required'] else 0)
            require(physical['flags'] == flags, 'Physical index flags mismatch')
            depth = max(node['depth'] for node in nodes)
            require(not candidate or depth >= table['candidate_min_depth'], 'Candidate did not reach planned depth')
            result['indexes'].append(dict(root=physical['root'], depth=depth, nodes=nodes, mapped_pages=sorted(mapped),
                leaf_entries=len(entries), distinct_keys=physical['entry_count'], physical_flags=physical['flags'],
                locator_key_sha256=hashlib.sha256(b''.join(entries)).hexdigest()))
        observations.append(result)
    return observations
