"""Current scalar row adapter; retained historical catalog decoder is unchanged."""
from multi_level_index_structure import catalog, require


def table_rows(data, table, pages):
    fixed, variables = catalog._row_layout(table['columns'])
    if variables != 1:
        return catalog._table_rows(data, table, pages)
    result = []
    for number in pages:
        page = catalog._page(data, number, 'scalar data')
        for entry in catalog._row_directory(page, number):
            if entry['hidden']: continue
            require(not entry['overflow'], 'Direct scalar row locator')
            raw = page[entry['start']:entry['end']]
            if len(raw) <= 255:
                decoded = catalog._decode_row(raw, table['columns'], 'scalar row')
            else:
                count = len(table['columns']); null_len = (count + 7) // 8
                require(raw[0] == count and len(raw) != 257, 'Single-variable row count/length')
                presence = raw[-null_len:]; count_position = len(raw) - null_len - 1
                require(raw[count_position] == 1, 'Single variable count')
                if count % 8: require(presence[-1] >> (count % 8) == 0, 'Unused presence bits')
                wide = len(raw) > 256; start = count_position - 2 - int(wide)
                jump = raw[count_position - 1] if wide else 255
                require(jump in (0, 1, 255), 'EXP-0245 transition ordinal')
                bounds = [raw[start + 1 - i] + (256 if jump != 255 and i >= jump else 0) for i in range(2)]
                require(bounds == [fixed, start] and start < 512, 'Exact bounded single-variable row boundaries')
                present = [bool(presence[i // 8] & (1 << (i % 8))) for i in range(count)]
                values = []
                for column in table['columns']:
                    ordinal = column['ordinal']
                    if column['type'] == 'Boolean' or not present[ordinal]: field = None
                    elif column['storage'] == 'fixed':
                        offset = 1 + column['fixed_offset']; field = raw[offset:offset + column['size']]
                    else: field = raw[fixed:start]
                    values.append(catalog._decode_value(column, field, present[ordinal]))
                decoded = dict(present=present, values=values)
            result.append(dict(page=number, row=entry['row'], **decoded))
    return result
