"""Helpers shared by the registry suites: files, table views over `structure`, and worker results.

Table views keep the shape the suite checks were written against (rows as ordinal lists,
index keys as column/direction) and add the plain-schema invariants those checks assume:
no deleted columns, sequential fixed and variable layouts, and exact row headers.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import dao
import structure

ROOT = dao.ROOT
REGISTRY = Path(__file__).resolve().parent
PAGE = structure.PAGE
NOTES = [[7, 'n' * 4096], [8, None]]
SYSTEM_TABLES = ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships']
identity = dao.identity


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write(path: Path, value) -> None:
    """Compact JSON; manifests are large and read by PowerShell."""
    Path(path).write_text(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n', encoding='utf-8')


def read(path: Path):
    return dao.read_json(path)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def page_bytes(data: bytes, number: int) -> bytes:
    return data[number * PAGE:(number + 1) * PAGE]


def check_provider(result: dict) -> None:
    dao.check_environment(result['environment'])


# --- Table views ---------------------------------------------------------------------

def _plain(definition: dict) -> None:
    """Invariants of tables written without schema edits (EXP-0059)."""
    body = bytes.fromhex(definition['body_hex'])
    root = definition['root']
    require(definition['marker'] in (0x4e, 0x53), f'definition {root}: marker')
    require(structure.u16(body, 25) == structure.u16(body, 21) and structure.u16(body, 29) == 0,
            f'definition {root}: live column count and reserved word')
    fixed, variables = 0, 0
    for column in definition['columns']:
        require(column['storage_id'] == column['ordinal'] and column['name'], f'definition {root}: column {column["ordinal"]}')
        if column['storage'] == 'variable':
            require(column['variable_index'] == variables, f'definition {root}: variable sequence')
            variables += 1
        elif column['type'] != 'Boolean':
            require(column['fixed_offset'] == fixed, f'definition {root}: fixed offset sequence')
            fixed += column['size']
    require(variables == definition['variable_high_water'], f'definition {root}: variable count')
    groups = [g['column'] for g in definition['long_value_maps']]
    require(len(groups) == len(set(groups)), f'definition {root}: one long-value map per column')


def tables(data: bytes, names=('Items', 'Notes')) -> dict[str, dict]:
    """Definitions of the named tables plus their owned `data_pages` and `long_value_pages`."""
    found = structure.tables(data)
    result = {}
    for name in names:
        require(name in found, f'catalog lacks table {name}')
        table = found[name]
        definition = dict(table['definition'], data_pages=table['data_pages'], long_value_pages=table['long_value_pages'])
        _plain(definition)
        definition['physical_indexes'] = [
            dict(index, keys=[{'column': k['column'], 'direction': k['direction']} for k in index['keys']])
            for index in definition['physical_indexes']]
        result[name] = definition
    return result


def _row_layout(raw: bytes, columns: list[dict], variables: int, wide: bool) -> None:
    """Exact row framing: column count, presence padding, variable count and fixed-area end."""
    count = len(columns)
    trailer = len(raw) - (count + 7) // 8
    fixed = 1 + max((c['fixed_offset'] + c['size'] for c in columns if c['storage'] == 'fixed' and c['type'] != 'Boolean'), default=0)
    require(raw[0] == count, 'row column count')
    require(not count % 8 or not raw[-1] >> (count % 8), 'unused presence bits')
    if not variables:
        require(trailer == fixed, 'row fixed boundary')
    else:
        position = trailer - 1
        require(raw[position] == variables, 'row variable count')
        end = position - (len(raw) - 1) // 256 - variables - 1
        jumps = raw[end + variables + 1:position]
        require(raw[end + variables] + 256 * sum(j != 255 and j == 0 for j in jumps) == fixed, 'row fixed boundary')
    require(wide or len(raw) <= 255 or (variables == 1 and len(raw) <= 511 and len(raw) != 257), 'row within the narrow layout')


def table_rows(data: bytes, table: dict, direct: bool = True, wide: bool = False) -> list[dict]:
    """Live rows as {page, row, raw_hex, values and payload descriptors by ordinal}; Memo/OLE
    values are complete payload hex.

    `direct` forbids overflow rows; without `wide`, rows over 255 bytes need one variable column.
    """
    columns = table['columns']
    variables = sum(c['storage'] == 'variable' for c in columns)
    result = []
    for row in structure.rows(data, {'name': str(table['root']), 'definition': table, 'data_pages': table['data_pages']}):
        _row_layout(bytes.fromhex(row['raw_hex']), columns, variables, wide)
        require(not direct or row['storage'] == row['locator'], 'direct row storage')
        result.append({'page': row['locator']['page'], 'row': row['locator']['row'], 'raw_hex': row['raw_hex'],
                       'values': [row['values'][c['name']] for c in columns],
                       'descriptors': {c['ordinal']: row['descriptors'][c['name']] for c in columns if c['name'] in row['descriptors']}})
    return result


def map_pages(data: bytes, where: dict) -> set[int]:
    return structure.map_record(data, where, 'map')[1]


def global_free(data: bytes) -> set[int]:
    return structure.map_record(data, {'page': 1, 'row': 0}, 'global')[1]


def tree(data: bytes, root: int, owner: int, fields, exact: bool = False) -> tuple[list[dict], list[bytes]]:
    """`structure.tree` without empty branches; `exact` also requires EXP-0062 header classes
    and separators equal to their child's maximum entry."""
    nodes, entries = structure.tree(data, root, owner, fields)
    for node in nodes:
        branch = bool(node['children'])
        require(node['entries'] or not branch, 'Empty index branch')
        require(not exact or (node['header_class'] == int(branch) and not node['stale_separators']), 'Exact branch separators')
    return nodes, entries


def long_tree(data: bytes, root: int, owner: int, descending: bool = False):
    """A single-Long-key tree (EXP-0062/0225); branches may retain separators (EXP-0225)."""
    nodes, entries = tree(data, root, owner, [(4, descending)])
    require(all(node['header_class'] in ((1, 2) if node['children'] else (0,)) for node in nodes), 'Long index node header')
    require(all(len(entry) == 9 for entry in entries), 'Long index record width')
    return nodes, entries


def long_key(value: int, descending: bool = False) -> bytes:
    encoded = b'\x7f' + ((value & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')
    return bytes(b ^ 255 for b in encoded) if descending else encoded


def locator_bytes(page: int, row: int) -> bytes:
    return page.to_bytes(3, 'big') + bytes([row])


def notes_identity(data: bytes) -> dict[str, str]:
    """Hashes of every Notes definition, map and owned page."""
    notes = tables(data, ['Notes'])['Notes']
    pages = set(notes['pages'])
    for where in [*notes['maps'].values(), *(g[role] for g in notes['long_value_maps'] for role in ('owned', 'available'))]:
        pages.add(where['page'])
        pages |= map_pages(data, where)
    return {str(p): sha(page_bytes(data, p)) for p in sorted(pages)}


# --- DAO results ---------------------------------------------------------------------

def aggregate(outbox: Path, names: list[str], document_type: str) -> dict:
    """Combines per-case worker results (one x86 process per case) into result.json."""
    index_path = outbox / 'workers.json'
    workers = read(index_path)
    require(workers['document_type'] == 'dao_workers' and [w['name'] for w in workers['workers']] == names, 'Worker inventory')
    combined = None
    for worker in workers['workers']:
        require(worker['file'] == worker['name'] + '-result.json', 'Worker file name')
        path = outbox / worker['file']
        require(worker['image'] is not None and identity(path) == {k: worker['image'][k] for k in ('size', 'sha256')},
                'Worker result identity: ' + worker['name'])
        result = read(path)
        require(result['document_type'] == document_type and all(result[k] == workers[k] for k in ('source_revision', 'manifest_sha256', 'round')),
                'Worker source/manifest/round')
        require([c['name'] for c in result['cases']] == [worker['name']], 'One complete worker case')
        if combined is None:
            combined = dict(result, cases=[], retention_failures=[], error=None, worker_index=identity(index_path), worker_results=[])
        require(result['environment'] == combined['environment'], 'Identical native provider environment')
        combined['cases'].extend(result['cases'])
        combined['retention_failures'].extend(result['retention_failures'])
        combined['worker_results'].append(dict(name=worker['name'], image=worker['image'], exit_code=worker['exit_code']))
        if result['error'] is not None or (worker['exit_code'] != 0 and all(c['status'] == 'pass' for c in result['cases'])):
            combined['error'] = 'One or more native workers failed; see retained worker results'
    require(combined is not None, 'Nonempty worker inventory')
    write(outbox / 'result.json', combined)
    return combined


def check_result(result: dict, manifest_path: Path, document_type: str, source_revision: str) -> None:
    """Producer binding: type, manifest, source revision, completion, retention and provider."""
    require(result['document_type'] == document_type and result['manifest_sha256'] == identity(manifest_path)['sha256']
            and result['source_revision'] == source_revision, 'Producer type, manifest and source revision')
    require(result['error'] is None and result['retention_failures'] == [], 'Producer and retention completed')
    check_provider(result)
