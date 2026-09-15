"""EXP-0225 tree bounds, including separators retained by native deletion."""
from multi_level_index_structure import catalog, require


def tree(data, root, owner):
    nodes, seen, leaf_entries = [], set(), []

    def visit(number, depth):
        require(depth <= 32 and number not in seen, 'Repeated page or excessive index depth')
        seen.add(number)
        page = catalog._page(data, number, 'index node')
        branch = page[0] == 3
        require(page[0] in (3, 4) and page[1] == 1 and page[21] in ((1, 2) if branch else (0,)), 'Index node header')
        require(int.from_bytes(page[4:8], 'little') == owner, 'Index owner mismatch')
        prefix = page[20]
        previous, following, tail = [int.from_bytes(page[offset:offset + 4], 'little') for offset in (8, 12, 16)]
        require(bool(tail) == branch, 'Index tail child mismatch')
        area = page[248:]
        boundaries = [position * 8 + bit for position, byte in enumerate(page[22:248]) for bit in range(8) if byte & (1 << bit)]
        require(all(prefix < end <= 1800 for end in boundaries), 'Index boundary outside entry area')
        require(int.from_bytes(page[2:4], 'little') == 1800 - (boundaries[-1] if boundaries else 0), 'Index free space mismatch')
        require(bool(boundaries) or (not branch and prefix == 0), 'Empty branch or unmatched prefix')
        node = dict(page=number, depth=depth, previous=previous, next=following, tail=tail, prefix=prefix,
                    entries=len(boundaries), children=[], header_class=page[21], stale_separators=0)
        nodes.append(node)
        start, entries = prefix, []
        for end in boundaries:
            entry = area[:prefix] + area[start:end]
            require(len(entry) == (13 if branch else 9), 'Invalid Long index record width')
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
