"""Numeric tree grammar from EXP-0062/0148/0150 and EXP-0225 upper fences."""
from multi_level_index_structure import catalog, require


WIDTHS = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 4, 7: 8, 8: 8}


def record_width(record, fields):
    offset = 0
    for kind, descending in fields:
        require(offset < len(record), 'Missing numeric component')
        marker = record[offset] ^ (255 if descending else 0)
        require(marker in (0, 127) and not (kind == 1 and marker == 0), 'Numeric component marker')
        offset += 1
        if not marker: continue
        if kind == 9:
            for chunk in range(32):
                require(offset + 9 <= len(record), 'Incomplete Binary chunk')
                suffix = record[offset + 8]; payload = record[offset:offset + 8]; offset += 9
                if suffix == 9: continue
                width = suffix ^ (255 if descending else 0)
                require(1 <= width <= 8 and chunk * 8 + width <= 255, 'Binary terminal length')
                require(payload[width:] == bytes([255 if descending else 0]) * (8 - width), 'Binary padding')
                break
            else: raise ValueError('Unterminated Binary component')
        else: offset += WIDTHS[kind]
    return offset + 4


def tree(data, root, owner, fields):
    nodes, seen, leaf_entries = [], set(), []

    def visit(number, depth):
        require(depth <= 32 and number not in seen, 'Repeated page or excessive index depth')
        seen.add(number)
        page = catalog._page(data, number, 'index node')
        branch = page[0] == 3
        require(page[0] in (3, 4) and page[1] == 1 and page[21] in ((1, 2, 3) if branch else (0,)), 'Index node header')
        require(int.from_bytes(page[4:8], 'little') == owner, 'Index owner mismatch')
        prefix = page[20]
        previous, following, tail = [int.from_bytes(page[offset:offset + 4], 'little') for offset in (8, 12, 16)]
        require(bool(tail) == branch, 'Index tail child mismatch')
        area = page[248:]
        boundaries = [position * 8 + bit for position, byte in enumerate(page[22:248]) for bit in range(8) if byte & (1 << bit)]
        require(all(prefix < end <= 1800 for end in boundaries), 'Index boundary outside entry area')
        require(int.from_bytes(page[2:4], 'little') == 1800 - (boundaries[-1] if boundaries else 0), 'Index free space mismatch')
        # EXP-0246: a class-one root can retain only its tail child after deletion.
        require(bool(boundaries) or prefix == 0, 'Empty node with unmatched prefix')
        require(bool(boundaries) or not branch or (depth == 1 and page[21] == 1), 'Unobserved empty intermediate node')
        node = dict(page=number, depth=depth, previous=previous, next=following, tail=tail, prefix=prefix,
                    entries=len(boundaries), children=[], header_class=page[21], stale_separators=0)
        nodes.append(node)
        start, entries = prefix, []
        for end in boundaries:
            entry = area[:prefix] + area[start:end]
            # Full leaf keys are independently rebuilt from physical row values by the caller.
            # At the 255-byte cap, the CRC obscures the remaining component grammar.
            shortened = any(kind == 9 for kind, _ in fields) and len(entry) == 259 + (4 if branch else 0)
            require(shortened or len(entry) == record_width(entry, fields) + (4 if branch else 0), 'Invalid scalar record width')
            entries.append(entry)
            start = end
        require(entries == sorted(entries), 'Unsorted complete index entries')
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
        require(all(low is not None and high is not None for low, high, _ in children), 'Empty index child')
        require(len({height for _, _, height in children}) == 1, 'Unequal child subtree heights')
        for position, entry in enumerate(entries):
            maximum, next_minimum = children[position][1], children[position + 1][0]
            require(maximum <= entry[:-4] < next_minimum, 'Invalid branch separator bounds')
            node['stale_separators'] += int(maximum != entry[:-4])
        node['subtree_height'] = children[0][2] + 1
        return children[0][0], children[-1][1], node['subtree_height']

    visit(root, 1)
    for depth in sorted({node['depth'] for node in nodes}):
        level = [node for node in nodes if node['depth'] == depth]
        for position, node in enumerate(level):
            require(node['previous'] == (level[position - 1]['page'] if position else 0)
                    and node['next'] == (level[position + 1]['page'] if position + 1 < len(level) else 0), 'Index sibling chain mismatch')
    require(len({node['depth'] for node in nodes if not node['children']}) == 1, 'Unequal leaf depth')
    require(leaf_entries == sorted(leaf_entries), 'Index traversal is not sorted')
    return nodes, leaf_entries
