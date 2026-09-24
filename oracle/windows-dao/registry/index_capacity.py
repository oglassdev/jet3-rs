"""Index capacity (EXP-0252/0253): tables with 4, 13, 14 and 32 Long indexes of up to ten
components, and a mixed ten-type composite key, through edits, regrowth and native
successors; see `registry.scalar`. A thirteenth-index duplicate must be refused unchanged.
"""

from __future__ import annotations

import json

from registry import common, scalar
from registry.common import require
from registry.numeric_indexes import SIZES, unique_queries

SCRIPT = common.REGISTRY / 'index_capacity.ps1'
CONFIG = {'indexes4': (4, 3), 'indexes13': (13, 9), 'indexes14': (14, 10), 'indexes32': (32, 10), 'mixed10': (4, 10)}
MIXED_TYPES = [4, 4, 2, 3, 5, 6, 7, 8, 1, 15, 10]
MIXED_ALPHABET = b'aA\xe9\xc9\xc6\xe6\xdf\x8a\x9a'


class Capacity(scalar.Scalar):
    MANIFEST = 'index-capacity.json'
    CASE_NAMES = tuple(CONFIG)
    SUMMARY = 'index capacity'

    def initial_row(self, name, id):
        if name == 'mixed10':
            text = bytes(MIXED_ALPHABET[(n * 7 + id) % len(MIXED_ALPHABET)] for n in range(240 + id % 16))
            guid = bytes((n * 37 + id) % 256 for n in range(16))
            return [id, id * 2 + 1 if id % 11 else None, id % 251, id % 30000 - 15000, id * 10001,
                    id % 1024 + 0.5, id + 0.25, 36526 + id % 1000 / 4, id % 2 == 0,
                    guid.hex() if id % 13 else None, text.hex()]
        width = CONFIG[name][1]
        return [id] + [None if id == 0 or (c == 1 and id % 11 == 0) or (c == width and id % 13 == 0) else id * (c + 1) + c for c in range(1, 11)]

    def recipe(self, name):
        count, width = CONFIG[name]
        types = MIXED_TYPES if name == 'mixed10' else [4] * 11
        fields = [[n, t, SIZES[t]] for n, t in zip(['Id'] + list('ABCDEFGHIJ'), types)]
        indexes = []
        for i in range(count):
            columns = [[0, False]] if i == 0 else [[1 + (c + i - 1) % width, bool((i >> (c % 5)) & 1)] for c in range(width)]
            indexes.append(dict(name='ById' if i == 0 else f'K{i:02}', primary=i == 0, unique=i in (0, count - 1), required=i == 0,
                                ignore=i != 0 and (i == count - 1 or i % 2 == 0), fields=columns))

        def insert(id):
            return dict(kind='insert', row=self.initial_row(name, id))

        def field(id, column, value):
            return dict(kind='field', id=id, column=column, value=value)

        replacement = self.initial_row(name, 42)
        replacement[0] = 3
        unique_queries(indexes, [self.initial_row(name, id) for id in (8, 1000, 9000, 1234567, 12345)])
        return dict(name=name, fields=fields, indexes=indexes, initial_rows=[self.initial_row(name, id) for id in range(24)],
                    stages=[dict(name='original', operations=[]),
                            dict(name='edited', operations=[insert(40), field(1, 1, None), dict(kind='replace', id=3, row=replacement),
                                                            field(7, 0, 99), dict(kind='delete', id=0)]),
                            dict(name='regrown', operations=[dict(kind='delete', id=2), insert(1000), insert(1001)])],
                    native=[insert(9000), field(9000, 0, 9001), dict(kind='delete', id=1000)])

    def refusal_check(self, directory, notes):
        receipts = json.loads((directory / 'refusals.json').read_text())
        require([r['name'] for r in receipts] == ['later-insert', 'later-replace'], 'Late-index refusal inventory')
        source = (directory / 'indexes13-edited.mdb').read_bytes()
        for receipt in receipts:
            before = directory / f"refusal-{receipt['name']}-before.mdb"
            after = directory / f"refusal-{receipt['name']}-after.mdb"
            require(receipt['preserved'] and receipt['error'] == 'Unsupported("duplicate unique key")'
                    and before.read_bytes() == after.read_bytes() == source, 'Thirteenth-index duplicate error and whole-image preservation')
            require(common.notes_identity(after.read_bytes()) == notes, 'Refusal preserves Notes-owned pages')
        return receipts


scalar.bind(globals(), Capacity())
