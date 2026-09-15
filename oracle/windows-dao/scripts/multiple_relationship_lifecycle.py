#!/usr/bin/env python3
"""Recheck EXP-0274 from retained inputs and DAO captures; never edit evidence.

Usage: multiple_relationship_lifecycle.py ARTIFACT_DIR NEW_REPORT
The directory contains matrix.json, extras-plan.json, candidate/, control/,
extras/, extras-staging/, outbox/, extras-outbox/, refusals-outbox/, and runs/.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import multiple_relationship_structure as raw
import multiple_relationship_storage as storage

REVISION = 'bd5a1b16b15d34c5cc4c5cb6017dcdaabcad0807'
PROVIDER = {
    'version': '3.6', 'provider': 'DAO.DBEngine.36', 'bits': 32,
    'culture': 'en-US', 'dll_version': '03.60.9765.0',
    'os': 'Microsoft Windows NT 10.0.20348.0',
    'dll_sha256': '4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac',
}

def read(path):
    return json.loads(path.read_text())


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def apply(model, operation):
    if operation['kind'] == 'replace':
        row = next(r for r in model[operation['table']] if r['Id'] == operation['id'])
        row.clear()
        row.update(copy.deepcopy(operation['values']))
    else:
        raw.apply(model, operation)


def lean(value):
    if isinstance(value, dict):
        return {k: lean(v) for k, v in value.items() if k != 'properties'}
    if isinstance(value, list):
        return [lean(v) for v in value]
    return value


def inventory(items, keys, wanted, label):
    ids = [tuple(item[k] for k in keys) for item in items]
    require(len(ids) == len(set(ids)) and set(ids) == set(wanted), label)
    return dict(zip(ids, items))


def prefix(observation):
    return {name: [(i['first_word'], i['second_word'], i['entries_hex']) for i in indexes]
            for name, indexes in observation['physical_indexes'].items()}


class Evaluation:
    def __init__(self, base):
        self.base = base
        self.captures = {}
        self.baselines = {}
        self.pairs = []
        self.refusals = []
        self.native_refusals = []
        self.payloads = 0
        self.run_ids = []

    def gate(self, run_file, directory, producer, bundle):
        run = (self.base / run_file).read_text().strip()
        retained = self.base / 'runs' / run
        outbox = retained / 'outbox'
        require((outbox / 'exit.txt').read_text().strip() == '0', run + ' process exit')
        require((outbox / 'log.txt').read_text() == '', run + ' clean log')
        require((retained / 'inbox/script.ps1').read_bytes() ==
                (Path(__file__).parent / producer).read_bytes(), run + ' producer bytes')
        require((retained / 'inbox' / bundle).read_bytes() == (self.base / bundle).read_bytes(), run + ' input bundle')
        with zipfile.ZipFile(self.base / bundle) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
            require(len(names) == len(set(names)), run + ' duplicate archive members')
            for name in names:
                if bundle == 'inputs.zip':
                    current = self.base / name
                elif name.startswith('candidate/'):
                    current = self.base / 'extras' / name.removeprefix('candidate/')
                elif name == 'extras-plan.json':
                    current = self.base / name
                else:
                    current = self.base / 'extras-staging' / name
                require(current.resolve().is_relative_to(self.base), run + ' input path')
                require(archive.read(name) == current.read_bytes(), run + ' input member/' + name)
        selected = self.base / directory
        require({p.name for p in selected.iterdir() if p.is_file()} ==
                {p.name for p in outbox.iterdir() if p.is_file()}, run + ' retained file inventory')
        for path in selected.iterdir():
            if path.is_file():
                require(path.read_bytes() == (outbox / path.name).read_bytes(), run + '/' + path.name)
        report = read(selected / 'report.json')
        require(report['status'] == 'pass' and report['error'] is None, run + ' worker')
        require(report['environment'] == PROVIDER, run + ' provider environment')
        self.run_ids.append(run)
        return report

    def capture(self, path, capture, case, model, source):
        label = str(path.relative_to(self.base))
        observation = raw.observe(path, capture, case, model, label)
        snapshot = capture['snapshot']
        self.schema(snapshot, case, label)
        require(snapshot['version'] == '3.0', label + ' database version')
        # Check all values in every indexed traversal, and each Seek result independently.
        for table in snapshot['user_tables']:
            rows = {r['Id']: r for r in table['rows']}
            for name, index in table['index_reads'].items():
                for row in index['traversal']:
                    require(row == rows[row['Id']], label + ' traversal values/' + name)
                for seek in index['seek']:
                    matches = [r for r in rows.values() if r[index['field']] == seek['query']]
                    require((seek['row'] is None) == (not matches), label + ' Seek presence/' + name)
                    if matches:
                        require(seek['row'] in matches, label + ' Seek value/' + name)
        if source not in self.baselines:
            self.baselines[source] = (storage.meta(source), storage.pages(source))
        metadata, system = self.baselines[source]
        require(storage.meta(path) == metadata, label + ' columns/logical records/properties')
        require(storage.pages(path) == system, label + ' system catalog storage')
        if path not in self.captures:
            self.payloads += storage.check(path)
        self.captures[path] = raw.ident(path)
        return observation

    def schema(self, snapshot, case, label):
        wanted_relations = [dict(name=r['name'], table=r['table'], foreign_table=r['foreign_table'],
                                 attributes=0, fields=[dict(name=r['field'], foreign_name=r['foreign_field'])])
                            for r in case['relations']]
        require(sorted(lean(snapshot['relations']), key=lambda x: x['name']) == sorted(wanted_relations, key=lambda x: x['name']), label + ' DAO relationship inventory')
        require([t['name'] for t in snapshot['user_tables']] == [t['name'] for t in case['tables']], label + ' ordered user tables')
        for table, wanted in zip(snapshot['user_tables'], case['tables']):
            require(table['attributes'] == 0, label + ' table attributes')
            fields, indexes = lean(table['fields']), lean(table['indexes'])
            if len(fields) == 1 and isinstance(fields[0], list):
                fields = fields[0]
            if len(indexes) == 1 and isinstance(indexes[0], list):
                indexes = indexes[0]
            expected = [dict(name=f['name'], type=f['type'], size=f['size'], ordinal=n,
                             attributes=1 if f['type'] == 4 else 2, required=False,
                             allow_zero_length=f['type'] in (10, 12)) for n, f in enumerate(wanted['fields'])]
            require(fields == expected, label + ' DAO fields/' + wanted['name'])
            specs = [("By" + wanted['name'], 'Id', True)]
            specs += [(r['name'], r['foreign_field'], False) for r in case['relations'] if r['foreign_table'] == wanted['name']]
            expected = [dict(name=n, primary=primary, unique=primary, required=primary, foreign=not primary,
                             ignore_nulls=False, fields=[dict(name=f, attributes=0)]) for n, f, primary in specs]
            require(sorted(indexes, key=lambda x: x['name']) == sorted(expected, key=lambda x: x['name']), label + ' DAO indexes/' + wanted['name'])
            require(set(table['index_reads']) == {n for n, _, _ in specs}, label + ' complete DAO index reads')

    def pair(self, left_path, left, right_path, right, case, model, source):
        a = self.capture(left_path, left, case, model, source)
        b = self.capture(right_path, right, case, model, source)
        require(left['snapshot'] == right['snapshot'], str(left_path) + ' complete DAO snapshot')
        require(prefix(a) == prefix(b), str(left_path) + ' native prefixes/keys/locators')
        self.pairs.append({'candidate': str(left_path.relative_to(self.base)),
                           'control': str(right_path.relative_to(self.base))})
        return a, b

    def operation(self, receipt, wanted, before, after, number=None):
        require(receipt['request'] == wanted, 'operation request')
        require(receipt['before'] == raw.ident(before), str(before) + ' operation input')
        require(receipt['after'] == raw.ident(after), str(after) + ' operation result')
        if number is None:
            require(receipt['status'] == 'success' and receipt['error'] is None, 'operation success')
        else:
            require(receipt['status'] == 'rejected' and receipt['error']['numbers'] == [number],
                    'native refusal code')

    def rust_refusal(self, directory, source, spec):
        records = read(directory / 'refusals.json')
        names = [r['name'] for r in records]
        if directory.name.startswith('self-primary-'):
            expected = ['primary-only']
            require((directory / 'original.mdb').read_bytes() == source.read_bytes(), str(directory) + ' corrected refusal source')
        elif directory.name.startswith('shared-'):
            expected = ['orphan-both', 'delete-referenced-a', 'key-only-parent-a']
        else:
            matrix = read(self.base / 'matrix.json')
            case_name = directory.name.rsplit('-r', 1)[0]
            expected = [r['name'] for g in matrix['graphs'] if g['name'] == case_name for r in g['refusals']]
        require(names == expected, str(directory) + ' exact Rust refusal inventory')
        found = [r for r in records if r['name'] == spec['name']]
        require(len(found) == 1 and found[0]['preserved'] is True and
                found[0]['error'].startswith('RelationshipConstraint {'), str(directory) + ' constraint refusal')
        path = directory / ('refusal-' + spec['name'] + '.mdb')
        require(path.read_bytes() == source.read_bytes(), str(path) + ' exact refusal')
        self.refusals.append({'path': str(path.relative_to(self.base)), 'identity': raw.ident(path),
                              'error': found[0]['error']})

    def main_graphs(self, report, matrix):
        require(report['environment'] == PROVIDER, 'provider environment')
        cases = inventory(report['cases'], ['case', 'replica'],
                          [(g['name'], r) for g in matrix['graphs'] for r in (1, 2)], 'main case inventory')
        for case in matrix['graphs']:
            for replica in (1, 2):
                stem = f"{case['name']}-r{replica}"
                record = cases[(case['name'], replica)]
                control = read(self.base / 'control' / (stem + '.json'))
                require(control['status'] == 'pass' and control['error'] is None, stem + ' native worker')
                require(all(control['environment'][k] == v for k, v in PROVIDER.items()), stem + ' native provider')
                require(len(record['candidate']) == len(control['stages']) == 5, stem + ' stages')
                model = {t['name']: copy.deepcopy(t['rows']) for t in case['tables']}
                source = self.base / 'candidate' / stem / 'original.mdb'
                require(source.read_bytes() == (self.base / 'control' / (stem + '-original.mdb')).read_bytes(), stem + ' common source')
                for ordinal, capture in enumerate(record['candidate']):
                    stage = 'original' if ordinal == 0 else f'success-{ordinal}'
                    native = control['stages'][ordinal]
                    if ordinal:
                        apply(model, case['success'][ordinal - 1])
                        prior = 'original' if ordinal == 1 else f'success-{ordinal - 1}'
                        self.operation(native['operation'], case['success'][ordinal - 1],
                                       self.base / 'control' / f'{stem}-{prior}.mdb',
                                       self.base / 'control' / f'{stem}-{stage}.mdb')
                        native = native['capture']
                    self.pair(self.base / 'outbox' / f'{stem}-{stage}-candidate.mdb', capture,
                              self.base / 'control' / f'{stem}-{stage}.mdb', native, case, model, source)
                refs = inventory(record['refusals'], ['name'], [(r['name'],) for r in case['refusals']], stem + ' refusal inventory')
                for spec in case['refusals']:
                    self.rust_refusal(source.parent, source, spec)
                    cap = refs[(spec['name'],)]['capture']
                    initial = {t['name']: copy.deepcopy(t['rows']) for t in case['tables']}
                    self.capture(self.base / 'outbox' / f"{stem}-refusal-{spec['name']}-candidate.mdb", cap, case, initial, source)
                reverse = copy.deepcopy(case['success'][0])
                original_table = next(t for t in case['tables'] if t['name'] == reverse['table'])
                reverse['value'] = next(r for r in original_table['rows'] if r['Id'] == reverse['id'])[reverse['column']]
                apply(model, reverse)
                for role in ('candidate', 'control'):
                    before = self.base / ('candidate' if role == 'candidate' else 'control')
                    before = before / stem / 'success-4.mdb' if role == 'candidate' else before / f'{stem}-success-4.mdb'
                    self.operation(record[role + '_successor']['operation'], reverse, before,
                                   self.base / 'outbox' / f'{stem}-successor-{role}.mdb')
                self.pair(self.base / 'outbox' / f'{stem}-successor-candidate.mdb', record['candidate_successor']['capture'],
                          self.base / 'outbox' / f'{stem}-successor-control.mdb', record['control_successor']['capture'], case, model, source)

    def extras(self, report, plan, native_report):
        cases = inventory(report['cases'], ['name', 'replica'],
                          [(p['name'], r) for p in plan['plans'] for r in (1, 2)], 'extra cases')
        native = inventory(native_report['refusals'], ['case', 'replica', 'name'],
                           [(p['name'], r, s['name']) for p in plan['plans'] for r in (1, 2) for s in p['refusals']], 'native refusals')
        for item in plan['plans']:
            for replica in (1, 2):
                stem = f"{item['source_prefix']}-r{replica}"
                record = cases[(item['name'], replica)]
                case = item['case']
                source = self.base / 'extras-staging/sources' / (stem + '.mdb')
                model = {t['name']: copy.deepcopy(t['rows']) for t in case['tables']}
                require(len(record['stages']) == len(item['stages']), stem + ' extra stage count')
                models = {}
                prior = source
                for spec, stage in zip(item['stages'], record['stages']):
                    require(stage['name'] == spec['name'], stem + ' stage identity')
                    require(len(stage['control_operations']) == len(spec['ops']) <= 1, stem + ' stage operations')
                    control = self.base / 'extras-outbox' / f"{stem}-{spec['name']}-control.mdb"
                    for operation, receipt in zip(spec['ops'], stage['control_operations']):
                        self.operation(receipt, operation, prior, control)
                        apply(model, operation)
                    models[spec['name']] = copy.deepcopy(model)
                    self.pair(self.base / 'extras-outbox' / f"{stem}-{spec['name']}-candidate.mdb", stage['candidate'],
                              control, stage['control'], case, model, source)
                    prior = control
                refs = inventory(record['refusals'], ['name'], [(r['name'],) for r in item['refusals']], stem + ' extra refusals')
                for spec in item['refusals']:
                    source_stage = spec.get('source_stage', 'original')
                    if source_stage == 'original':
                        rust_source = native_source = source
                        directory = self.base / 'extras' / stem
                    else:
                        directory = self.base / 'extras' / f'self-primary-r{replica}'
                        rust_source = self.base / 'extras' / stem / (source_stage + '.mdb')
                        native_source = self.base / 'extras-staging/control-pre' / f'{stem}-{source_stage}.mdb'
                    self.rust_refusal(directory, rust_source, spec)
                    result = native[(item['name'], replica, spec['name'])]
                    path = self.base / 'refusals-outbox' / f"{stem}-refusal-{spec['name']}.mdb"
                    self.operation(result['operation'], spec['operation'], native_source, path, spec['number'])
                    cap = result['capture']
                    require(refs[(spec['name'],)]['capture']['snapshot'] == cap['snapshot'], stem + ' repeated refusal snapshot')
                    obs = self.capture(path, cap, case, models[source_stage], source)
                    data, before = path.read_bytes(), native_source.read_bytes()
                    if spec['number'] == 3200:
                        require(data == before, stem + ' native parent refusal bytes')
                    else:
                        changed = [(i, x, y) for i, (x, y) in enumerate(zip(before, data)) if x != y]
                        require(len(before) == len(data), stem + ' failed native length')
                        analyzed = raw.catalog.analyze_checkpoint(before)
                        child = next(t for t in analyzed['tables'].values() if t['name'] == 'Child')['definition']
                        # EXP-0273 shared physical FK1: one decrement in each prefix word.
                        offset = child['root'] * 2048 + 43 + 8
                        require(changed == [(1538, 0, 1), (offset, 3, 2), (offset + 4, 3, 2)], stem + ' shared native failure bytes')
                        require([(i['first_word'], i['second_word']) for i in obs['physical_indexes']['Child']] == [(0, 3), (2, 2)], stem + ' shared prefix once')
                    self.native_refusals.append({'path': str(path.relative_to(self.base)), 'identity': raw.ident(path), 'number': spec['number']})
                successors = inventory(record['successors'], ['role'], [('candidate',), ('control',)], stem + ' successors')
                apply(model, item['successor'])
                for role in ('candidate', 'control'):
                    before = self.base / 'extras-outbox' / f"{stem}-{item['stages'][-1]['name']}-{role}.mdb"
                    self.operation(successors[(role,)]['operation'], item['successor'], before,
                                   self.base / 'extras-outbox' / f'{stem}-successor-{role}.mdb')
                self.pair(self.base / 'extras-outbox' / f'{stem}-successor-candidate.mdb', successors[('candidate',)]['capture'],
                          self.base / 'extras-outbox' / f'{stem}-successor-control.mdb', successors[('control',)]['capture'], case, model, source)


def main():
    base, output = map(Path, sys.argv[1:])
    base = base.resolve()
    require(not output.exists(), 'report output already exists')
    evaluation = Evaluation(base)
    result = {'document_type': 'multiple_relationship_portable_acceptance', 'source_revision': REVISION,
              'status': 'rejected', 'errors': []}
    try:
        identity = read(base / 'identity.json')
        require(identity['source_revision'] == REVISION, 'candidate revision')
        main_report = evaluation.gate('run-id.txt', 'outbox', 'multiple_relationship_capture.ps1', 'inputs.zip')
        extras_report = evaluation.gate('extras-run-id-r2.txt', 'extras-outbox', 'multiple_relationship_extras.ps1', 'extras-input.zip')
        refusal_report = evaluation.gate('refusals-run-id.txt', 'refusals-outbox', 'multiple_relationship_refusals.ps1', 'extras-input.zip')
        evaluation.main_graphs(main_report, read(base / 'matrix.json'))
        evaluation.extras(extras_report, read(base / 'extras-plan.json'), refusal_report)
        require(len(evaluation.pairs) == 92 and len(evaluation.refusals) == 28 and len(evaluation.native_refusals) == 8, 'complete comparison inventory')
        for directory in ('outbox', 'extras-outbox', 'refusals-outbox'):
            paths = set((base / directory).iterdir())
            expected = {p for p in evaluation.captures if p.parent == base / directory}
            expected |= {base / directory / name for name in ('report.json', 'log.txt', 'exit.txt')}
            require(paths == expected, directory + ' exact retained output inventory')
        result['status'] = 'accepted'
    except Exception as error:
        result['errors'].append(f'{type(error).__name__}: {error}')
    result.update(runs=evaluation.run_ids, pairs=evaluation.pairs, rust_refusals=evaluation.refusals,
                  native_refusals=evaluation.native_refusals, payload_descriptors=evaluation.payloads,
                  captures={str(k.relative_to(base)): v for k, v in sorted(evaluation.captures.items())})
    output.write_text(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n')
    print(json.dumps({'status': result['status'], 'errors': result['errors'], 'pairs': len(evaluation.pairs),
                      'captures': len(evaluation.captures), 'rust_refusals': len(evaluation.refusals),
                      'native_refusals': len(evaluation.native_refusals), 'payload_descriptors': evaluation.payloads}))
    return 0 if result['status'] == 'accepted' else 1


if __name__ == '__main__':
    raise SystemExit(main())
