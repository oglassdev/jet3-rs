"""Bounded first-row placement comparison for EXP-0285's empty legacy inputs."""
import copy

import required_column_acceptance as acceptance
import required_column_discovery as discovery
import relationship_system_indexes as systems

require = discovery.require
catalog = discovery.catalog


def observe(path):
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    table = next(table for table in analysis['tables'].values() if table['name'] == 'Rows')
    definition = table['definition']
    rows = discovery.raw_rows.rows(data, table)
    index = definition['physical_indexes'][0]
    nodes, entries = discovery.indexes.tree(data, index['root'], definition['root'], [(4, False)])
    require(entries == sorted(discovery.long_key(row['values']['Id'], row['locator']) for row in rows), 'complete first-row index keys')
    allocation = acceptance.allocation_observation(data, analysis, [node['page'] for node in nodes], systems.inventory(path))
    return data, definition, rows, nodes, allocation


def changed_bit(raw_hex, page, present):
    raw = bytearray.fromhex(raw_hex)
    require(raw[0] == 0, 'observed inline placement map')
    bit = page - int.from_bytes(raw[1:5], 'little')
    require(0 <= bit < (len(raw) - 5) * 8, 'placement bit within map')
    offset, mask = 5 + bit // 8, 1 << (bit % 8)
    require(bool(raw[offset] & mask) != present, 'one actual membership transition')
    raw[offset] = raw[offset] | mask if present else raw[offset] & (255 ^ mask)
    return raw.hex()


def check_empty_insert(before_path, rust_path, native_path):
    before, definition, rows, _, baseline = observe(before_path)
    require(rows == [] and len(baseline['free_pages']) == 1, 'empty input with one released property page')
    require(len(definition['physical_indexes']) == 1, 'one primary tree')
    eof = len(before) // 2048
    released = baseline['free_pages'][0]
    results = []
    for role, path, page in [('rust', rust_path, eof), ('native', native_path, released)]:
        data, current, rows, nodes, allocation = observe(path)
        require(len(data) == len(before) + (2048 if role == 'rust' else 0), 'exact append or free-page reuse')
        require(len(rows) == 1 and rows[0]['locator'] == rows[0]['storage'] == {'page': page, 'row': 0}, 'new row on selected page')
        require(len(nodes) == 1 and nodes[0]['depth'] == 1 and nodes[0]['entries'] == 1, 'single unchanged primary leaf')
        require(set(allocation['maps']) == set(baseline['maps']), 'same complete map inventory')
        changed_maps = []
        for key, original in baseline['maps'].items():
            expected = copy.deepcopy(original)
            if key in ('Rows/table/owned', 'Rows/table/available'):
                require(original['members'] == [], 'empty table maps')
                expected['members'] = [page]
                expected['raw_hex'] = changed_bit(original['raw_hex'], page, True)
                changed_maps.append(original['locator'])
            require(allocation['maps'][key] == expected, 'exact allocation transition: ' + key)
        expected = copy.deepcopy(baseline['global'])
        expected['raw_hex'] = changed_bit(expected['raw_hex'], page, False)
        expected['members'] = [released] if role == 'rust' else []
        require(allocation['global'] == expected and allocation['free_pages'] == expected['members'], 'exact global free transition')
        require(allocation['system_page_hashes'] == baseline['system_page_hashes'], 'unchanged system pages')
        restored = bytearray(data[:len(before)])
        for locator in [*changed_maps, {'page': 1, 'row': 0}]:
            image = catalog._page(before, locator['page'], 'unchanged map framing')
            entry = catalog._row_directory(image, locator['page'])[locator['row']]
            raw = image[entry['start']:entry['end']]
            bit = page - int.from_bytes(raw[1:5], 'little')
            offset = locator['page'] * 2048 + entry['start'] + 5 + bit // 8
            restored[offset] ^= 1 << (bit % 8)
        offsets = [(definition['row_count_offset'], 4)]
        offsets += [(index['entry_count_offset'] - 4, 8) for index in definition['physical_indexes']]
        for offset, length in offsets:
            restored[offset:offset + length] = before[offset:offset + length]
        restored[1538] = before[1538]
        for number in range(eof):
            if number not in (nodes[0]['page'], page):
                require(restored[number * 2048:(number + 1) * 2048] == before[number * 2048:(number + 1) * 2048], 'only requested row, tree, counters and allocation bits changed')
        header = bytearray(data[:2048])
        require(header[1538] == (before[1538] if role == 'rust' else 4) and before[1538] == 2, 'observed native header state')
        header[1538] = before[1538]
        require(header == before[:2048], 'all other header bytes unchanged')
        row = copy.deepcopy(rows[0])
        row.pop('locator'); row.pop('storage')
        results.append((current, row, nodes))
    require(results[0] == results[1], 'exact schema, row bytes and tree metadata outside new-row location')
    return {'rust_data_page': eof, 'native_data_page': released, 'source_free_pages': [released]}
