"""Independent complete raw system-index key and locator inventory."""
import hashlib
import importlib.util
from pathlib import Path

import system_catalog as catalog
import text_index_collation as text_keys

# Bounded oracle-analysis capacities required by the largest retained graph.
catalog.MAX_PAGES = 8192
catalog.MAX_ROWS_PER_PAGE = 1019
catalog.MAX_TABLES = 64
catalog.MAX_COLUMNS = 255
catalog.MAX_TEXT = 10000

SYSTEM_INDEXES = {
    'MSysObjects': [(0, 1, [(1, 'ParentId', 'Long', 1), (2, 'Name', 'Text', 1)]),
                    (1, 1, [(0, 'Id', 'Long', 1)])],
    'MSysACEs': [(0, 8, [(0, 'ObjectId', 'Long', 1)])],
    'MSysQueries': [(0, 1, [(0, 'ObjectId', 'Long', 1), (1, 'Attribute', 'Byte', 1),
                           (2, 'Order', 'Binary', 1)])],
    'MSysRelationships': [(0, 2, [(0, 'szRelationship', 'Text', 1)]),
                          (1, 2, [(4, 'szObject', 'Text', 1)]),
                          (2, 2, [(6, 'szReferencedObject', 'Text', 1)])],
}


def req(ok, message):
    if not ok:
        raise AssertionError(message)


def _tree_module():
    source = Path(__file__).resolve().parent / 'numeric_index_mutation_structure.py'
    spec = importlib.util.spec_from_file_location('relationship_index_system_tree', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def record_width(record, fields):
        offset = 0
        for kind in fields:
            req(record[offset] == 127, 'present system key component')
            offset = record.index(0, offset + 1) + 1 if kind == 'Text' else offset + 5
        return offset + 4

    module.record_width = record_width
    module.shortened_record = lambda record, fields, branch: False
    return module


def long_key(value):
    return b'\x7f' + ((value & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')


def system_key(value):
    if isinstance(value, str):
        return text_keys.component(value.encode('cp1252').hex(), False)
    req(isinstance(value, int), 'system key type')
    return long_key(value)


def inventory(path):
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    named = {table['name']: table for table in analysis['tables'].values()}
    req(set(analysis['system_rows']) == set(SYSTEM_INDEXES), 'complete system row inventory')
    tree = _tree_module()
    result = {}
    for name, expected_schema in SYSTEM_INDEXES.items():
        definition = named[name]['definition']
        rows = analysis['system_rows'][name]['rows']
        schema = [(index['index'], index['flags'],
                   [(field['column'], definition['columns'][field['column']]['name'],
                     definition['columns'][field['column']]['type'], field['direction'])
                    for field in index['keys']]) for index in definition['physical_indexes']]
        req(schema == expected_schema, 'complete system index schema/' + name)
        if name == 'MSysQueries':
            req(not rows, 'stored queries outside this suite')
        indexes = []
        for index in definition['physical_indexes']:
            columns = [field['column'] for field in index['keys']]
            req(all(field['direction'] == 1 for field in index['keys']), 'system key direction')
            kinds = [definition['columns'][column]['type'] for column in columns]
            nodes, entries = tree.tree(data, index['root'], definition['root'], kinds)
            expected = sorted(
                b''.join(system_key(row['values'][column]) for column in columns)
                + row['page'].to_bytes(3, 'big') + bytes([row['row']])
                for row in rows
            )
            req(entries == expected, 'complete system keys and locators/' + name)
            members = sorted(catalog._locator_pages(data, index['map'], name + ' system index map'))
            req({node['page'] for node in nodes} <= set(members), 'system index node ownership/' + name)
            first = int.from_bytes(bytes.fromhex(index['prefix_hex']), 'little')
            second = index['entry_count']
            expected_second = len({entry[:-4] for entry in entries})
            indexes.append({'ordinal': index['index'], 'flags': index['flags'], 'root': index['root'],
                            'map': index['map'], 'members': members, 'nodes': [node['page'] for node in nodes],
                            'first_word': first, 'second_word': second, 'expected_second_word': expected_second,
                            'prefix_matches_live_keys': first == 0 and second == expected_second,
                            'entries': [entry.hex() for entry in entries]})
        result[name] = {'schema': schema, 'definition_row_count': definition['row_count'],
                        'live_row_count': len(rows), 'indexes': indexes}
    return result
