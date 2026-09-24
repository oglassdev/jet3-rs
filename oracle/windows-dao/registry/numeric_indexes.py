"""Numeric and multiple-index mutations (EXP-0230/0232/0243/0245/0246/0248/0250).

Seven cases cover integral, Currency/Double/Single, deep variable-width, Date, Binary, Text
and GUID keys across three indexes each (primary, composite pair and a descending last
column), through growth, edits, collapse and regrowth; see `registry.scalar`.
"""

from __future__ import annotations

import json

from registry import common, scalar
from registry.common import require

SCRIPT = common.REGISTRY / 'numeric_indexes.ps1'
SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 4, 7: 8, 8: 8, 9: 255, 10: 255, 15: 16}
DATES = [-2.75, -1.25, 0.0, 0.25, 0.5, 1.75, 36526.125, 36527.875]
TEXT_WIDTHS = [1, 7, 8, 16, 63, 127, 168, 169, 223, 224, 247, 248, 249, 250, 251, 252, 253, 254, 255]
TEXT_ALPHABET = b"aAezZ -'\xe9\xc9\xc6\xe6\xdf\x8a\x9a\xa0\x00\x1f"
BINARY_WIDTHS = [1, 7, 8, 9, 17, 223, 224, 225, 247, 248, 249, 250, 251, 252, 253, 254, 255]
NATIVE_DELETE = {'integral': 195, 'wide': 120, 'deep': 8, 'dates': 1000, 'binary': 1000, 'text': 1000, 'guid': 1000}
SAMPLE_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 80, 120, 195, 324, 500, 999, 1000, 9000, 9001, 1234567, 1234568]


def field(id, column, value):
    return dict(kind='field', id=id, column=column, value=value)


def replace(id, row):
    return dict(kind='replace', id=id, row=row)


def index(name, columns, unique=False, primary=False, ignore=False):
    return dict(name=name, fields=columns, unique=unique, primary=primary, required=primary, ignore=ignore)


def unique_queries(indexes, samples):
    """Present full-key Seek arguments; nullable entries are checked in the full traversal."""
    for entry in indexes:
        queries = [[row[c] for c, _ in entry['fields']] for row in samples]
        entry['queries'] = list({json.dumps(q): q for q in queries if all(v is not None for v in q)}.values())


class Numeric(scalar.Scalar):
    MANIFEST = 'numeric-index-mutation.json'
    CASE_NAMES = ('integral', 'wide', 'deep', 'dates', 'binary', 'text', 'guid')

    def initial_row(self, name, id):
        if name == 'integral':
            return [id, id % 7 if id % 11 else None, id % 5 - 2 if id % 13 else None, id % 2 == 0]
        if name == 'wide':
            return [id, id * 10001 if id % 17 else None, id + 0.5 if id % 19 else None, None]
        if name == 'dates':
            return [id, DATES[id % 8] if id % 11 else None, 36526 + id / 4 if id % 19 else None, id % 13]
        if name == 'text':
            payload = bytes(TEXT_ALPHABET[(offset * 7 + id % 23) % len(TEXT_ALPHABET)] for offset in range(TEXT_WIDTHS[id % len(TEXT_WIDTHS)]))
            return [id, payload.hex() if id % 19 else None, id]
        if name == 'guid':
            payload = bytes((offset * 37 + id % 251) % 256 for offset in range(16))
            return [id, payload.hex() if id % 19 else None, id]
        if name == 'binary':
            payload = bytes((offset * 37 + id % 23) % 256 for offset in range(BINARY_WIDTHS[id % len(BINARY_WIDTHS)]))
            return [id, payload.hex() if id % 19 else None, id]
        if id < 3:
            return [id, None, None]
        if id == 3:
            return [id, None, 1.5]
        if id == 4:
            return [id, -100000, None]
        return [id, id, id + 0.5]

    def recipe(self, name):
        if name == 'integral':
            types, count, additions, deletions, regrown = [4, 2, 3, 1], 195, range(195, 325), range(195), range(1000, 1195)
            edits = [field(0, 1, 250), field(1, 2, None), replace(2, [2, None, -32768, False]),
                     replace(3, [3, None, None, True]), field(4, 3, False), field(324, 0, 999)]
        elif name == 'wide':
            types, count, additions, deletions, regrown = [4, 5, 7, 6], 80, range(80, 92), [0, 17, 80], [120, 121, 122]
            edits = [field(2, 3, -1.5), replace(3, [3, 30003, 3.5, 2.25]), field(4, 1, None),
                     replace(5, [5, None, None, None]), field(6, 2, None), field(7, 0, 99)]
        elif name in ('dates', 'binary', 'text', 'guid'):
            types = [4, 8, 8, 4] if name == 'dates' else [4, {'binary': 9, 'text': 10, 'guid': 15}[name], 4]
            count, additions, deletions, regrown = 96, range(96, 220), range(160), range(1000, 1040)
            if name == 'dates':
                edits = [field(1, 1, -1.75), field(2, 2, None), replace(3, [3, None, 36526.75, 3]), field(4, 3, -4)]
            elif name == 'binary':
                edits = [field(1, 1, 'ab'), field(2, 1, None), replace(3, [3, 'cd', 3]), field(4, 2, -40)]
            else:
                first, second = ('41e95a', '20') if name == 'text' else ('ab' * 16, 'cd' * 16)
                edits = [field(1, 1, first), field(2, 1, None), replace(3, [3, second, 3]), field(4, 2, -40)]
            edits.append(field(219, 0, 999))
        else:
            types, count, additions, deletions, regrown = [4, 5, 7], 5673, [5673], [5673], [6000]
            edits = [field(5673, 1, -50000), replace(6, [6, None, None]), field(5, 2, None), field(7, 2, -1.25)]

        def insert(id):
            return dict(kind='insert', row=self.initial_row(name, id))

        last = 1 if name in ('binary', 'text', 'guid') else 2 if name == 'deep' else 3
        indexes = [index('ById', [[0, False]], unique=True, primary=True),
                   index('ByPair', [[1, False], [2, True]], unique=name != 'integral', ignore=name == 'integral'),
                   index('ByLast', [[last, True]], unique=name == 'wide', ignore=name == 'wide')]
        samples = [self.initial_row(name, id) for id in SAMPLE_IDS]
        if name == 'wide':
            samples.extend([[0, 0, 0.5, n] for n in [-1.5, 2.25, 4.5]])
        unique_queries(indexes, samples)
        return dict(name=name, fields=[[n, t, SIZES[t]] for n, t in zip(['Id', 'A', 'B', 'C'], types)], indexes=indexes,
                    initial_rows=[self.initial_row(name, id) for id in range(count)],
                    stages=[dict(name='original', operations=[]), dict(name='grown', operations=[insert(id) for id in additions]),
                            dict(name='edited', operations=edits), dict(name='collapsed', operations=[dict(kind='delete', id=id) for id in deletions]),
                            dict(name='regrown', operations=[insert(id) for id in regrown])],
                    native=[insert(9000), field(9000, 0, 9001), dict(kind='delete', id=NATIVE_DELETE[name])])

    def stage_check(self, stem, layout, original_layout):
        indexes = layout['indexes']
        if stem == 'integral-edited':
            require(indexes['ByPair']['counter'] == 47 and indexes['ByPair']['distinct'] == 49, 'Edits increase ordinary distinct keys above retained counter')
        if stem == 'wide-edited':
            require(indexes['ByLast']['counter'] == 0 and indexes['ByLast']['distinct'] == 2, 'Edits create keys above zero retained counter')
        if stem == 'integral-grown':
            require(all(i['depth'] == 2 for i in indexes.values()), 'All three integral indexes cross leaves')
        if stem == 'wide-grown':
            require(indexes['ByPair']['depth'] == 2, 'Wide composite leaf growth')
        if stem == 'deep-original':
            require(indexes['ByPair']['depth'] == 2, 'Variable-width depth2 boundary')
        if stem == 'deep-grown':
            require(indexes['ByPair']['depth'] == 3, 'Variable-width depth3 transition')
        if stem == 'integral-regrown':
            new = [page for id, page, _ in layout['locators'] if id >= 1000]
            retained = [page for id, page, _ in layout['locators'] if id < 1000]
            require(min(new) < max(retained) and min(new) == min(original_layout['data_pages']), 'Higher IDs reuse released lower data page')

    def continuation_check(self, name, compressed):
        if name in ('integral', 'deep'):
            require(any(entry != 'ById' for entry in compressed), 'Native source contains prefix-compressed non-Long/composite nodes')

    def refusal_check(self, directory, notes):
        receipts = json.loads((directory / 'refusals.json').read_text())
        require([r['name'] for r in receipts] == ['later-insert', 'later-replace'], 'Later-index refusal inventory')
        source = (directory / 'wide-edited.mdb').read_bytes()
        for receipt in receipts:
            before = directory / f"refusal-{receipt['name']}-before.mdb"
            after = directory / f"refusal-{receipt['name']}-after.mdb"
            require(receipt['preserved'] and receipt['error'] == 'Unsupported("duplicate unique key")'
                    and before.read_bytes() == after.read_bytes() == source, 'Third-index duplicate error and byte preservation')
            require(common.notes_identity(after.read_bytes()) == notes, 'Refused operation Notes preservation')
        return receipts


scalar.bind(globals(), Numeric())
