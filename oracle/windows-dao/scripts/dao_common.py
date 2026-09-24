"""Small helpers shared by the DAO suites and scripts/dao-check.py."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import system_catalog as catalog


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return {'size': path.stat().st_size, 'sha256': digest(path)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def normalized(snapshot):
    result = copy.deepcopy(snapshot)
    result['user_tables'].sort(key=lambda table: table['name'])
    for table in result['user_tables']:
        table['rows'].sort()
    return result


def validate_environment(environment):
    provider = environment.get('accepted_provider')
    if (environment.get('document_type') != 'dao_environment'
            or environment.get('protocol_version') != '1.2.0'
            or environment.get('status') != 'ready'
            or environment.get('host', {}).get('process_architecture') != 'x86'
            or not isinstance(provider, dict)
            or provider.get('prog_id') != 'DAO.DBEngine.36'
            or provider.get('database_version') != 'dbVersion30'):
        raise ValueError('Retained environment is not the declared ready x86 DAO provider')


def load_catalog(name, **limits):
    """Load a separate system_catalog instance so limit overrides stay local."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name('system_catalog.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key, value in limits.items():
        setattr(module, key, value)
    return module


def require(condition, message):
    if not condition:
        raise ValueError(message)


def leaf_entries(data, root):
    """EXP-0062 leaf boundaries and shared prefix; keys remain uninterpreted."""
    page = catalog._page(data, root, 'relationship index')
    if page[0] != 4 or any(page[8:20]):
        raise catalog.DecodeError('Expected a single leaf with no sibling/child links')
    area = page[248:]
    prefix_length = int.from_bytes(page[20:22], 'little')
    ends = [bit for bit in range(len(area) + 1) if page[22 + bit // 8] & (1 << (bit % 8))]
    used = ends[-1] if ends else 0
    if prefix_length > used or int.from_bytes(page[2:4], 'little') != len(area) - used:
        raise catalog.DecodeError('Leaf prefix/free-space mismatch')
    entries = []
    start = prefix_length
    for end in ends:
        if end <= start:
            raise catalog.DecodeError('Leaf boundary order mismatch')
        raw = area[:prefix_length] + area[start:end]
        if end - start < 4 or len(raw) <= 4:
            raise catalog.DecodeError('Leaf entry lacks a row locator')
        entries.append({'key_hex': raw[:-4].hex(), 'row_page': int.from_bytes(raw[-4:-1], 'big'), 'row': raw[-1]})
        start = end
    return {'root': root, 'prefix_hex': area[:prefix_length].hex(), 'entries': entries}


def index_tree(catalog, data, root, owner):
    """Decode a complete index tree; `catalog` is an isolated system_catalog instance."""
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
        node = dict(page=number, depth=depth, previous=previous, next=following, tail=tail, prefix=prefix, entries=len(boundaries), children=[], header_class=page[21])
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
            node['subtree_height'] = 0
            return (entries[-1] if entries else None), 0
        child_heights = []
        for entry in entries:
            child = int.from_bytes(entry[-4:], 'big')
            node['children'].append(child)
            maximum, height = visit(child, depth + 1)
            child_heights.append(height)
            require(maximum == entry[:-4], 'Branch separator is not child maximum key and locator')
        node['children'].append(tail)
        maximum, height = visit(tail, depth + 1)
        require(all(child == height for child in child_heights), 'Unequal child subtree heights')
        node['subtree_height'] = height + 1
        return maximum, height + 1
    visit(root, 1)
    for depth in sorted({node['depth'] for node in nodes}):
        level = [node for node in nodes if node['depth'] == depth]
        for position, node in enumerate(level):
            require(node['previous'] == (level[position - 1]['page'] if position else 0)
                    and node['next'] == (level[position + 1]['page'] if position + 1 < len(level) else 0), 'Index sibling chain mismatch')
    require(len({node['depth'] for node in nodes if not node['children']}) == 1, 'Unequal leaf depth')
    require(leaf_entries == sorted(leaf_entries), 'Index traversal is not sorted')
    return nodes, leaf_entries
