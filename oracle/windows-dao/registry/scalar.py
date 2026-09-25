"""Shared scalar-row mutation engine for the numeric, capacity and wide-row suites.

A suite subclasses `Scalar` with its recipe. Each case builds a DAO control, replays four
stages against Rust candidates (one DAO worker process per case, see Rows.ps1), then applies
native successor writes to both outputs. The continuation round lets Rust edit the native
DAO successor. Every image is decoded independently: rows, index trees with complete
key/locator entries, maps and retained counters (EXP-0230), and validated by jet3-cli.
"""

from __future__ import annotations

import copy
from pathlib import Path
import shutil
import struct
import uuid

import keys
import recipes
import structure
from registry import common
from registry.common import identity, require

NOTES_SCHEMA = [['Id', 4, 4], ['Body', 12, 0]]
TABLES = ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes']
DOCUMENT = 'dao_scalar_mutation_result'
KINDS = {1: 'boolean', 2: 'byte', 3: 'integer', 4: 'long', 5: 'currency', 6: 'single', 7: 'double', 8: 'date_time',
         9: 'binary', 10: 'text', 11: 'long_binary', 12: 'memo', 15: 'guid'}
DUPLICATE = 'duplicate unique key'


def cell(value, kind):
    """A recipe value as a jet3-cli cell (Currency scaled; Binary, Text, Memo, OLE and GUID hex)."""
    if value is None:
        return None
    return {KINDS[kind]: list(bytes.fromhex(value)) if kind in (9, 10, 11, 12, 15) else value}


def cells(row, case):
    return [cell(value, field[1]) for value, field in zip(row, case['fields'])]


def index_request(case, index):
    """A recipe index as a jet3-cli create request index."""
    fields = [dict(column=case['fields'][c][0], **({'direction': 'descending'} if d else {})) for c, d in index['fields']]
    kind = 'primary' if index['primary'] else 'unique' if index['unique'] else 'ordinary'
    return dict(name=index['name'], kind=kind, fields=fields, **({'null_policy': 'ignore_all_null'} if index['ignore'] else {}))


NOTES_TABLE = dict(name='Notes', columns=[dict(name='Id', type='long'), dict(name='Body', type='memo')], indexes=[],
                   rows=[[{'long': 7}, {'memo': 'n' * 4096}], [{'long': 8}, None]])


def write(case, row, id=None, **extra):
    """An insert of `row`, or a replacement of the row whose Id is `id`, as a jet3-cli step."""
    request = dict(operation='insert' if id is None else 'replace', table='Items', values=cells(row, case))
    return dict(request=request, **({} if id is None else dict(locate=dict(table='Items', id=id))), **extra)


class Scalar:
    MANIFEST: str
    CASE_NAMES: tuple
    FINAL = 'regrown'
    SUMMARY = 'numeric mutation'
    REFUSAL_SOURCE: str
    REFUSALS: tuple

    # --- Recipe ----------------------------------------------------------------------

    def initial_row(self, name, id):
        raise NotImplementedError

    def recipe(self, name):
        raise NotImplementedError

    def key(self, row, case, index):
        if index['ignore'] and all(row[c] is None for c, _ in index['fields']):
            return None
        parts = []
        for column, descending in index['fields']:
            kind, value = case['fields'][column][1], row[column]
            if kind == 10 and value is not None:
                value = bytes.fromhex(value)
            parts.append(keys.component(value, kind, descending))
        return keys.shorten(b''.join(parts))

    def counters_for(self, case, rows):
        return {index['name']: len({self.key(row, case, index) for row in rows.values()} - {None}) for index in case['indexes']}

    def apply(self, rows, operation, case, counters):
        kind = operation['kind']
        if kind == 'insert':
            row = operation['row'].copy()
            require(row[0] not in rows, 'Recipe duplicate Id')
            for index in case['indexes']:
                value = self.key(row, case, index)
                if value is not None and all(self.key(old, case, index) != value for old in rows.values()):
                    counters[index['name']] += 1
            rows[row[0]] = row
        elif kind == 'delete':
            del rows[operation['id']]
        else:
            old = rows.pop(operation['id'])
            row = operation['row'].copy() if kind == 'replace' else old
            if kind == 'field':
                row[operation['column']] = operation['value']
            require(row[0] not in rows, 'Recipe replacement Id')
            rows[row[0]] = row
        for index in case['indexes']:
            if not index['unique']:
                continue
            present = [self.key(row, case, index) for row in rows.values() if all(row[c] is not None for c, _ in index['fields'])]
            require(len(present) == len(set(present)), 'Recipe unique-key collision')

    def expected_stages(self, case):
        rows = {row[0]: row.copy() for row in case['initial_rows']}
        counters = self.counters_for(case, rows)
        for stage in case['stages']:
            for operation in stage['operations']:
                self.apply(rows, operation, case, counters)
            yield stage, copy.deepcopy(rows), counters.copy()

    # --- Rust candidates -------------------------------------------------------------

    def create_request(self, case):
        fixed = set(case.get('fixed_fields', ()))
        columns = [dict(name=name, type='fixed_text' if name in fixed else KINDS[kind], **({'size': size} if kind in (9, 10) else {}))
                   for name, kind, size in case['fields']]
        items = dict(name='Items', columns=columns, indexes=[index_request(case, index) for index in case['indexes']],
                     rows=[cells(r, case) for r in case['initial_rows']])
        return dict(tables=[items, NOTES_TABLE])

    def step(self, rows, operation, case, counters):
        """The jet3-cli mutation for one recipe operation, which is then applied to `rows`.

        Field edits involving Null, Boolean or variable-width values replace the whole row."""
        kind = operation['kind']
        if kind == 'insert':
            result = write(case, operation['row'])
        elif kind == 'replace':
            result = write(case, operation['row'], operation['id'])
        elif kind == 'delete':
            result = dict(request=dict(operation='delete', table='Items'), locate=dict(table='Items', id=operation['id']))
        else:
            column, value = operation['column'], operation['value']
            row = rows[operation['id']].copy()
            field_kind = case['fields'][column][1]
            if row[column] is None or value is None or field_kind in (1, 9, 10, 11, 12):
                row[column] = value
                result = write(case, row, operation['id'])
            else:
                request = dict(operation='update', table='Items', column=column, value=cell(value, field_kind))
                result = dict(request=request, locate=dict(table='Items', id=operation['id']))
        self.apply(rows, operation, case, counters)
        return result

    def refusal_images(self, case, source):
        """`refusal-<name>-before.mdb` and `-after.mdb` inputs taken from `source`."""
        raise NotImplementedError

    def refusal_pair(self, name, source, step, damage=None):
        before = f'refusal-{name}-before.mdb'
        return [{'file': before, 'from': source, 'steps': [{'edit': damage}] if damage else []},
                {'file': f'refusal-{name}-after.mdb', 'from': before, 'steps': [step]}]

    def candidates(self, spec=None):
        """Every `<case>-<stage>.mdb`, each continuing from the previous stage, and the refusal inputs."""
        images = []
        for name in self.CASE_NAMES:
            case = self.recipe(name)
            rows = {row[0]: row.copy() for row in case['initial_rows']}
            counters = self.counters_for(case, rows)
            previous = None
            for stage in case['stages']:
                file = f"{name}-{stage['name']}.mdb"
                steps = [self.step(rows, operation, case, counters) for operation in stage['operations']]
                if previous is None:
                    images.append({'file': file, 'steps': [dict(command='create', request=self.create_request(case)), *steps]})
                else:
                    images.append({'file': file, 'from': previous, 'steps': steps})
                previous = file
                if file == self.REFUSAL_SOURCE + '.mdb':
                    images += self.refusal_images(case, file)
        return images

    # --- Raw checks ------------------------------------------------------------------

    def raw_rows(self, data, table, case):
        """Decoded Items rows in the recipe's value model (Currency scaled, Text/GUID hex)."""
        rows = common.table_rows(data, table)
        for row in rows:
            for n, (_, kind, _) in enumerate(case['fields']):
                value = row['values'][n]
                if value is not None and kind in (5, 6, 7):
                    raw = bytes.fromhex(value['raw_hex'])
                    row['values'][n] = int.from_bytes(raw, 'little', signed=True) if kind == 5 else struct.unpack('<f' if kind == 6 else '<d', raw)[0]
                elif value is not None and kind == 10:
                    row['values'][n] = value.encode('cp1252').hex()
                elif value is not None and kind == 15:
                    row['values'][n] = uuid.UUID(bytes_le=bytes.fromhex(value['raw_hex'])).hex
        return rows

    def raw_extra(self, data, case, table, result):
        """Suite-specific additions to the raw layout."""

    def raw_check(self, data, case, expected, counters, previous=None):
        table = common.tables(data, ['Items'])['Items']
        pages = table['data_pages']
        rows = self.raw_rows(data, table, case)
        require(not table['long_value_pages'] and sorted(r['values'] for r in rows) == sorted(expected.values()), 'Complete raw scalar rows')
        require(table['row_count'] == len(expected), 'Declared live row count')
        require([[c['name'], c['type'], c['size']] for c in table['columns']]
                == [[name, structure.PHYSICAL_TYPES[kind], size] for name, kind, size in case['fields']], 'Raw field schema')
        require(len(table['physical_indexes']) == len(table['logical_indexes']) == len(case['indexes']), 'Complete physical/logical index inventory')
        logical = {index['name']: index['physical_index'] for index in table['logical_indexes']}
        require(set(logical) == {i['name'] for i in case['indexes']} and len(set(logical.values())) == len(case['indexes']), 'Distinct named indexes')
        map_slots = [tuple(physical['map'][k] for k in ('page', 'row')) for physical in table['physical_indexes']]
        require(len(set(map_slots)) == len(map_slots), 'Distinct index map slots')
        all_owned = set(pages)
        results = {}
        for index in case['indexes']:
            physical = table['physical_indexes'][logical[index['name']]]
            require(physical['keys'] == [dict(column=c, direction=int(not d)) for c, d in index['fields']]
                    and physical['flags'] == int(index['unique']) + 2 * int(index['ignore']) + 8 * int(index['required']), 'Raw index schema: ' + index['name'])
            nodes, entries = structure.tree(data, physical['root'], table['root'], [(case['fields'][c][1], d) for c, d in index['fields']])
            wanted = sorted(k + common.locator_bytes(r['page'], r['row']) for r in rows if (k := self.key(r['values'], case, index)) is not None)
            require(entries == wanted, 'Full sorted key+locator records: ' + index['name'])
            owned = common.map_pages(data, physical['map'])
            reached = {node['page'] for node in nodes}
            require(reached <= owned and not owned.intersection(all_owned), 'Independent data/index maps')
            all_owned.update(owned)
            for page in owned - reached:
                image = common.page_bytes(data, page)
                require(image[0] in (3, 4) and int.from_bytes(image[4:8], 'little') == table['root'], 'Reserved node owner')
                if previous is not None:
                    require(image == common.page_bytes(previous, page), 'Reserved node bytes preserved')
            require(physical['entry_count'] == counters[index['name']], 'EXP-0230 insertion-only absent-key counter: ' + index['name'])
            layout = dict(depth=max(n['depth'] for n in nodes), nodes=sorted(reached), counter=physical['entry_count'],
                          entries=len(entries), distinct=len({e[:-4] for e in entries}), compressed=[n['page'] for n in nodes if n['prefix']],
                          stale_separators=sum(n['stale_separators'] for n in nodes), reserved=sorted(owned - reached))
            results[index['name']] = layout
        locators = sorted([r['values'][0], r['page'], r['row']] for r in rows)
        result = dict(map_slots=map_slots, indexes=results, data_pages=sorted(pages), file_pages=len(data) // common.PAGE, locators=locators)
        self.raw_extra(data, case, table, result)
        return result

    def refused_input(self, source, receipt, before):
        """The exact refusal input expected from `source`; adds suite details to `receipt`."""
        require(DUPLICATE in receipt['error'], 'Duplicate unique key refusal: ' + receipt['name'])
        return source

    def refusal_check(self, directory, notes, errors):
        """Receipts of the refused writes (with their jet3-cli errors) that preserved every byte."""
        require(len(errors) == len(self.REFUSALS), 'Refusal inventory')
        source = (directory / (self.REFUSAL_SOURCE + '.mdb')).read_bytes()
        receipts = []
        for name, error in zip(self.REFUSALS, errors):
            before = (directory / f'refusal-{name}-before.mdb').read_bytes()
            after = (directory / f'refusal-{name}-after.mdb').read_bytes()
            receipt = dict(name=name, error=error, preserved=True)
            require(before == after == self.refused_input(source, receipt, before), 'Refused write preserves the whole image: ' + name)
            require(common.notes_identity(after) == notes, 'Refusal preserves Notes-owned pages')
            receipts.append(receipt)
        return receipts

    def stage_check(self, stem, layout, original_layout):
        """Suite-specific boundary assertions for one prepared stage."""

    def continuation_check(self, name, compressed):
        """Suite-specific requirements on the native continuation source."""

    # --- DAO comparison --------------------------------------------------------------

    def normalized(self, capture, case, rows):
        require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
        value = copy.deepcopy(capture['snapshot'])
        require(value['version'] == '3.0' and value['tables'] == TABLES and value['queries'] == value['relations'] == [], 'Complete DAO database inventory')
        require([t['name'] for t in value['user_tables']] == ['Items', 'Notes'], 'DAO user inventory')
        items, notes = value['user_tables']
        for table, fields in [(items, case['fields']), (notes, NOTES_SCHEMA)]:
            require(table['attributes'] == 0 and [[f['name'], f['type'], f['size']] for f in table['fields']] == fields, 'Full DAO scalar schema')
        require(sorted(items['rows']) == sorted(rows.values()) and sorted(notes['rows']) == common.NOTES, 'Complete DAO rows and Memo content')
        expected_indexes = [dict(name=i['name'], primary=i['primary'], unique=i['unique'], required=i['required'], ignore_nulls=i['ignore'], foreign=False,
                                 fields=[dict(name=case['fields'][c][0], attributes=int(d)) for c, d in i['fields']]) for i in case['indexes']]
        require(sorted(items['indexes'], key=lambda i: i['name']) == sorted(expected_indexes, key=lambda i: i['name']) and notes['indexes'] == [],
                'Full DAO index schema')
        require(set(value['index_reads']) == {i['name'] for i in case['indexes']}, 'Complete DAO index traversal inventory')
        for index in case['indexes']:
            actual = value['index_reads'][index['name']]
            selected = [r for r in rows.values() if self.key(r, case, index) is not None]
            require(sorted(actual['traversal']) == sorted(selected)
                    and [self.key(r, case, index) for r in actual['traversal']] == sorted(self.key(r, case, index) for r in selected),
                    'Complete directed DAO traversal')
            require([s['query'] for s in actual['seek']] == index['queries'], 'Finite full-key Seek inventory')
            for seek in actual['seek']:
                query_row = [None] * len(case['fields'])
                for (column, _), query_value in zip(index['fields'], seek['query']):
                    query_row[column] = query_value
                wanted_key = self.key(query_row, case, index)
                matches = [r for r in selected if self.key(r, case, index) == wanted_key]
                require(seek['row'] in matches if matches else seek['row'] is None, 'Seek returns complete matching row or absence')
                # Equal-key ties can select different physical rows in independently allocated files.
                seek['matches'] = sorted(matches)
                del seek['row']
            actual['traversal'].sort(key=lambda r: (self.key(r, case, index), r[0]))
        items['rows'].sort()
        notes['rows'].sort()
        items['indexes'].sort(key=lambda i: i['name'])
        return value

    def retained_capture(self, outbox, capture, case, rows, counters, notes, previous=None):
        path = outbox / capture['file']
        data = path.read_bytes()
        image = identity(path)
        require(capture['before'] == capture['after'] == image, 'Read-only capture and retained image identity')
        require(common.notes_identity(data) == notes, 'Notes-owned pages preserved')
        snapshot = self.normalized(capture, case, rows)
        layout = self.raw_check(data, case, rows, counters, previous)
        return snapshot, dict(image=image, layout=layout), data

    # --- Registry interface ----------------------------------------------------------

    def prepare(self, images: Path, revision: str, spec: dict, results: dict) -> None:
        cases = [self.recipe(name) for name in self.CASE_NAMES]
        for case in cases:
            previous = baseline_notes = original_layout = None
            for stage, expected, counters in self.expected_stages(case):
                stem = f"{case['name']}-{stage['name']}"
                data = (images / (stem + '.mdb')).read_bytes()
                common.validate(images / (stem + '.mdb'))
                layout = self.raw_check(data, case, expected, counters, previous if len(stage['operations']) == 1 else None)
                if baseline_notes is None:
                    baseline_notes = common.notes_identity(data)
                    original_layout = layout
                require(common.notes_identity(data) == baseline_notes, 'All Notes metadata/data/LVAL page hashes: ' + stem)
                self.stage_check(stem, layout, original_layout)
                stage['counters'] = counters
                previous = data
            case['notes_pages'] = baseline_notes
        errors = [results[f'refusal-{name}-after.mdb'][-1]['refused'] for name in self.REFUSALS]
        refusals = self.refusal_check(images, self.refusal_notes(cases), errors)
        files = {p.name: identity(p) for p in sorted(images.iterdir()) if p.suffix == '.mdb'}
        common.write(images / self.MANIFEST, dict(document_type='scalar_mutation_inputs', round='mutations', source_revision=revision,
                                                  cases=cases, files=files, refusals=refusals))

    def refusal_notes(self, cases):
        return cases[1]['notes_pages']

    def aggregate(self, outbox: Path) -> None:
        common.aggregate(outbox, list(self.CASE_NAMES), DOCUMENT)

    def evaluate(self, images: Path, outbox: Path) -> dict:
        manifest_path = images / self.MANIFEST
        manifest = common.read(manifest_path)
        report = dict(status='failed', round=manifest['round'], source_revision=manifest['source_revision'],
                      manifest=identity(manifest_path), cases=[], error=None)
        try:
            result_path = outbox / 'result.json'
            result = common.read(result_path)
            report['result'] = identity(result_path)
            for name, pin in manifest['files'].items():
                require(identity(images / name) == identity(outbox / name) == pin, 'Retained input identity: ' + name)
            common.check_result(result, manifest_path, DOCUMENT, manifest['source_revision'])
            require(result['round'] == manifest['round'], 'Result round')
            report['environment'] = result['environment']
            require([c['name'] for c in result['cases']] == [c['name'] for c in manifest['cases']], 'Case inventory')
            for case, observed in zip(manifest['cases'], result['cases']):
                outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None)
                report['cases'].append(outcome)
                try:
                    require(observed['status'] == 'pass' and observed['error'] is None, 'Case completed')
                    if manifest['round'] == 'continuation':
                        self.compare_continuation(images, outbox, manifest, case, observed, outcome)
                    else:
                        self.compare_mutations(images, outbox, manifest, case, observed, outcome)
                    outcome['status'] = 'accepted'
                except Exception as error:
                    outcome['error'] = f'{type(error).__name__}: {error}'
            if manifest['round'] == 'mutations':
                errors = [r['error'] for r in manifest['refusals']]
                require(self.refusal_check(images, self.refusal_notes(manifest['cases']), errors) == manifest['refusals'], 'Refusal receipts')
                report['refusals'] = manifest['refusals']
            require(all(c['status'] == 'accepted' for c in report['cases']), f'One or more {self.SUMMARY} cases failed')
            report['status'] = 'accepted'
        except Exception as error:
            report['error'] = f'{type(error).__name__}: {error}'
        return report

    def compare_continuation(self, images, outbox, manifest, case, observed, outcome):
        rows = {r[0]: r for r in case['expected']}
        pairs, checkpoints = {}, {}
        require(observed['operation']['count'] == len(case['operations']) and observed['operation']['before'] == manifest['files'][case['source_file']],
                'Continuation operation input')
        for role in ('candidate', 'control'):
            pairs[role], checkpoints[role], _ = self.retained_capture(outbox, observed['roles'][role], case, rows, case['counters'], case['notes_pages'])
        require(checkpoints['candidate']['image'] == manifest['files'][case['candidate_file']] and checkpoints['control']['image'] == observed['operation']['after'],
                'Continuation output identities')
        require(pairs['candidate'] == pairs['control'], 'Paired continuation schema/rows/traversal/seeks')
        outcome['checkpoints'].append(dict(name='continued', roles=checkpoints, compressed_source=case['compressed_source']))

    def compare_mutations(self, images, outbox, manifest, case, observed, outcome):
        require([s['name'] for s in observed['stages']] == [s['name'] for s in case['stages']], 'Checkpoint inventory')
        created = outbox / observed['created']['file']
        chain = identity(created)
        require(chain == observed['created']['image'], 'Native creation image')
        native_notes = common.notes_identity(created.read_bytes())
        previous = None
        for (stage, rows, counters), capture in zip(self.expected_stages(case), observed['stages']):
            require(capture['operations'] == stage['operations'] and capture['mutation']['before'] == chain
                    and capture['mutation']['count'] == len(stage['operations']), 'Native operation chain')
            pairs, checkpoints = {}, {}
            stem = f"{case['name']}-{stage['name']}"
            for role in ('candidate', 'control'):
                candidate = role == 'candidate'
                pairs[role], checkpoints[role], data = self.retained_capture(
                    outbox, capture['roles'][role], case, rows, counters, case['notes_pages'] if candidate else native_notes,
                    previous if candidate and len(stage['operations']) == 1 else None)
                if candidate:
                    previous = data
            require(checkpoints['candidate']['image'] == manifest['files'][stem + '.mdb'] and checkpoints['control']['image'] == capture['mutation']['after'],
                    'Stage output identities')
            require(pairs['candidate'] == pairs['control'], 'Paired full DAO metadata and contents')
            chain = checkpoints['control']['image']
            outcome['checkpoints'].append(dict(name=stage['name'], roles=checkpoints))
        for operation in case['native']:
            self.apply(rows, operation, case, counters)
        native_pairs, native_details = {}, {}
        for role in ('candidate', 'control'):
            native = observed['native'][role]
            require(native['mutation']['count'] == len(case['native']) and native['mutation']['before'] == outcome['checkpoints'][-1]['roles'][role]['image'],
                    'Native successor source')
            native_pairs[role], native_details[role], _ = self.retained_capture(
                outbox, native['capture'], case, rows, counters, case['notes_pages'] if role == 'candidate' else native_notes)
            require(native['mutation']['after'] == native_details[role]['image'], 'Native successor output')
        require(native_pairs['candidate'] == native_pairs['control'], 'Native follow-up writes on both outputs')
        outcome['native'] = native_details

    def prepare_continue(self, images: Path, first_outbox: Path, output: Path, revision: str) -> None:
        first = common.read(images / self.MANIFEST)
        result = common.read(first_outbox / 'result.json')
        require(result['manifest_sha256'] == identity(images / self.MANIFEST)['sha256'], 'Continuation parent run')
        output.mkdir(parents=True, exist_ok=False)
        cases, receipts = [], []
        try:
            for name in self.CASE_NAMES:
                case = copy.deepcopy(next(c for c in first['cases'] if c['name'] == name))
                observed = next(c for c in result['cases'] if c['name'] == name)
                capture = observed['native']['control']['capture']
                source = first_outbox / capture['file']
                _, rows, counters = list(self.expected_stages(case))[-1]
                for operation in case['native']:
                    self.apply(rows, operation, case, counters)
                self.normalized(capture, case, rows)
                require(identity(source) == capture['before'] == capture['after'], 'Native continuation source identity')
                source_layout = self.raw_check(source.read_bytes(), case, rows, counters)
                compressed = {index: layout['compressed'] for index, layout in source_layout['indexes'].items() if layout['compressed']}
                self.continuation_check(name, compressed)
                operations = [dict(kind='insert', row=self.initial_row(name, 1234567)), dict(kind='field', id=1234567, column=0, value=1234568),
                              dict(kind='delete', id=9001)]
                source_name = name + '-continuation-source.mdb'
                shutil.copy2(source, output / source_name)
                file = name + '-continued.mdb'
                image = {'file': file, 'from': source_name, 'steps': [self.step(rows, operation, case, counters) for operation in operations]}
                results = recipes.build([image], output, output.parent / 'requests')[file]
                receipts.append(dict(name=name, source=identity(source), results=results))
                common.validate(output / file)
                continued = (output / file).read_bytes()
                self.raw_check(continued, case, rows, counters)
                notes = common.notes_identity(source.read_bytes())
                require(common.notes_identity(continued) == notes, 'Continuation Notes-owned bytes')
                case.update(source_file=source_name, candidate_file=name + '-continued.mdb', expected=sorted(rows.values()), counters=counters,
                            operations=operations, notes_pages=notes, compressed_source=compressed)
                cases.append(case)
            files = {p.name: identity(p) for p in sorted(output.iterdir()) if p.suffix == '.mdb'}
            common.write(output / self.MANIFEST, dict(document_type='scalar_mutation_inputs', round='continuation', source_revision=revision,
                                                      cases=cases, files=files, parent_manifest=identity(images / self.MANIFEST),
                                                      parent_result=identity(first_outbox / 'result.json')))
        finally:
            common.write(output / 'continuation-preparation.json', receipts)


def bind(module_globals: dict, engine: Scalar) -> None:
    """Exposes an engine as the registry module interface."""
    for name in ('MANIFEST', 'candidates', 'prepare', 'aggregate', 'evaluate', 'prepare_continue'):
        module_globals[name] = getattr(engine, name)
