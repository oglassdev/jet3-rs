"""Storage lifecycle suites: page release/reuse, slot churn, opaque-object preservation and
native failure/rollback images (EXP-0304, EXP-0311).

Stages of one run (every stage keeps its inputs, VM outboxes and logs under `out`):

    native      Native.ps1 builds each case and checkpoints every lifecycle stage.
    candidates  jet3-cli replays the same stages on a copy of each native original.
    refusals    jet3-cli applies each constraint refusal to the native original (preservation).
    edits       Native.ps1 continues both final images and captures native failures.
    observe     Observe.ps1 reads back every image.
    compare     one `lifecycle` pair per stage and case, one `refusal` pair per refusal.

Each lifecycle pair checks, for the Rust candidate and the native image separately: the
complete raw decoder (physical keys, locators, payload reachability, disjoint ownership,
allocation classification), strict Rust validation, expected raw values, byte-exact
unrelated tables/maps/pages against the native original, complete unassigned rows against
the previous stage, and the DAO readback (identity, QueryDefs, expected rows). It then
compares raw semantics and the complete DAO readback of both images; only Database.Name and
table-row enumeration order are normalized.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import shutil
import subprocess

import compare
from compare import require
import dao
import storage_plan as plan
import structure

ROLES = ('candidate', 'native')
NATIVE = dao.ORACLE / 'Native.ps1'


def normalized(view) -> dict:
    """A DAO observation without file identity, opened path or row enumeration order."""
    result = copy.deepcopy({k: v for k, v in view.items() if k not in ('file', 'identity')})
    for prop in result['database']['properties']:
        if prop['name'] == 'Name':
            prop['value'] = '<opened path>'
    for table in result['tables']:
        table['rows'] = sorted(table['rows'], key=lambda r: r['Id'])
    return result


def readback(view, baseline_view, expected, path: Path) -> None:
    """The observation is of `path`, keeps every QueryDef and holds exactly `expected` Items rows."""
    require(view['identity'] == dao.identity(path), 'readback identity: ' + path.name)
    require(view['querydefs'] == baseline_view['querydefs'], 'complete QueryDefs: ' + path.name)
    items, = [t for t in view['tables'] if t['name'] == 'Items']
    require(sorted(items['rows'], key=lambda r: r['Id']) == expected, 'complete expected DAO rows: ' + path.name)


def preserve(before, after, before_bytes: bytes, after_bytes: bytes) -> list[int]:
    """Every table but Items (system tables and Watch) keeps its definition, rows, maps and pages."""
    pages = set()
    for name, old in before['tables'].items():
        if name == 'Items':
            continue
        require(old == after['tables'][name], 'unrelated complete table: ' + name)
        pages.update(old['definition']['pages'])
        for role, record in before['maps'].items():
            if role.startswith(name + '/'):
                require(record == after['maps'][role], 'unrelated map: ' + role)
                pages.update(record['members'])
                pages.update(p for p in record['record']['references'] if p)
    for number in pages:
        require(compare.pages_equal(before_bytes, after_bytes, number), f'unrelated page {number}')
    return sorted(pages)


def surviving_rows(before, after, operations) -> list:
    """Rows the stage does not name keep their complete locator, storage, descriptors and bytes."""
    changed = {op['id'] for op in operations if 'id' in op}
    indexed = {r['values']['Id']: r for r in after['tables']['Items']['rows']}
    preserved = []
    for row in before['tables']['Items']['rows']:
        ident = row['values']['Id']
        if ident in changed:
            continue
        require(ident in indexed and row == indexed[ident], f'complete unassigned row/descriptor: {ident}')
        preserved.append(ident)
    return preserved


def storage(observed) -> dict:
    rows = observed['tables']['Items']['rows']
    return {'pages': observed['maps']['Items/table/owned']['members'], 'free_pages': observed['free_pages'],
            'size': observed['identity']['size'], 'overflow_rows': sum(r['locator'] != r['storage'] for r in rows),
            'maps': {k: v for k, v in observed['maps'].items() if k.startswith('Items/')}}


def reuse(released, reused) -> dict:
    require(not released['pages'], 'delete-all releases every table data page')
    require(reused['pages'] and set(reused['pages']) <= set(released['free_pages']), 'refill reuses previously free pages')
    require(reused['size'] == released['size'], 'refill reuses storage without file growth')
    return {'released_free_pages': len(released['free_pages']), 'reused_pages': reused['pages'], 'size': reused['size']}


def changed_bytes(before: bytes, after: bytes) -> dict:
    return {'before_size': len(before), 'after_size': len(after),
            'bytes': [{'offset': i, 'before': a, 'after': b} for i, (a, b) in enumerate(zip(before, after)) if a != b],
            'appended_hex': after[len(before):].hex(), 'removed_hex': before[len(after):].hex()}


def native_failure_bytes(before: bytes, baseline, kind: str, refusal: str) -> bytes:
    """Exact bytes DAO leaves after each refusal, with or without rollback (EXP-0304)."""
    expected = bytearray(before)
    if refusal == 'relationship':
        # Explicit rollback retains the transaction marker and these catalog counters.
        require(expected[1538] == 0, 'suite starts with zero transaction marker byte')
        expected[1538] = 2
        for name, delta in (('MSysObjects', 1), ('MSysACEs', 2)):
            definition = baseline['tables'][name]['definition']
            offsets = [(definition['row_count_offset'], delta)]
            offsets += [(index['entry_count_offset'], 1) for index in definition['physical_indexes']]
            for offset, increment in offsets:
                value = int.from_bytes(before[offset:offset + 4], 'little') + increment
                expected[offset:offset + 4] = value.to_bytes(4, 'little')
        return bytes(expected)
    if kind != 'wide':
        # Both payload schemas retain the transaction marker byte.
        require(expected[1538] == 0, 'suite transaction marker byte')
        expected[1538] = 2
    if kind == 'sparse' and refusal == 'duplicate':
        # EXP-0237: a refused explicit duplicate consumes an AutoNumber generator value.
        definition = baseline['tables']['Items']['definition']
        require(definition['marker'] == ord('N'), 'native AutoNumber definition')
        offset = definition['root'] * structure.PAGE + 16
        value = int.from_bytes(before[offset:offset + 4], 'little') + 1
        expected[offset:offset + 4] = value.to_bytes(4, 'little')
    return bytes(expected)


class StorageRun:
    def __init__(self, name: str, spec: dict, out: Path, context):
        self.name, self.spec, self.out, self.context = name, spec, out, context
        self.cases = plan.PLANS[spec['plan']]()
        self.refusals = bool(spec.get('refusals'))
        self.report = {'suite': name, 'source': context.source, 'provenance': spec.get('provenance', []),
                       'stages': [], 'pairs': [], 'outcome': 'failed'}
        self.images: dict[str, Path] = {}
        self.edits: dict[str, dict] = {}
        self.observations: dict[str, dict] = {}

    # Helpers
    def stage(self, name, function):
        record = {'name': name, 'status': 'failed'}
        self.report['stages'].append(record)
        try:
            detail = function()
            record['status'] = 'pass'
            if detail:
                record.update(detail)
        except Exception as error:
            record['error'] = f'{type(error).__name__}: {error}'
            raise dao.Stage(record['error']) from error

    def parallel(self, function, items):
        with ThreadPoolExecutor(max_workers=max(1, min(self.context.parallel, len(items)))) as pool:
            return list(pool.map(function, items))

    def cli(self, arguments, log: Path, request=None):
        if request is not None:
            dao.write_json(log.with_suffix('.request.json'), request)
            arguments = [*arguments, '--input', str(log.with_suffix('.request.json'))]
        done = subprocess.run([str(self.context.cli), *map(str, arguments)], capture_output=True, text=True)
        dao.write_json(log.with_suffix('.result.json'), {'arguments': list(map(str, arguments)), 'returncode': done.returncode,
                                                         'stdout': done.stdout, 'stderr': done.stderr})
        return done

    def request_for(self, path: Path, op: dict) -> dict:
        """A jet3-cli request; a Seek-style `id` becomes the row's page/slot locator."""
        request = copy.deepcopy(op)
        ident = request.pop('id', None)
        if ident is not None:
            data = path.read_bytes()
            table = structure.tables(data)[op['table']]
            matches = [r for r in structure.rows(data, table) if r['values']['Id'] == ident]
            require(len(matches) == 1, f'{path.name}: expected one {op["table"]} row with Id {ident}')
            request['row'] = {'page': matches[0]['locator']['page'], 'slot': matches[0]['locator']['row']}
        return request

    def image(self, name: str) -> Path:
        if name not in self.images:
            raise dao.Stage(f'missing image {name}')
        return self.images[name]

    def stage_path(self, role: str, case: dict, stage: str) -> Path:
        return self.image(f'{role}-{case["name"]}-{stage}.mdb')

    # Native lifecycles
    def native(self):
        def build(case):
            directory = self.out / 'native' / case['name']
            directory.mkdir(parents=True)
            dao.write_json(directory / 'native.json', {'inputs': [plan.native_input(case)], 'edits': []})
            self.context.vm.run(NATIVE, [directory / 'native.json'],
                                f'{self.name}-native-{case["name"]}', directory)
            result = dao.read_json(directory / 'native-result.json')
            dao.check_environment(result['environment'])
            built, = result['inputs']
            require(built['failure'] is None, f'native {case["name"]} failed: {built["failure"]}')
            expected = [f'native-{case["name"]}-{s}.mdb' for s in ['original', *(s['name'] for s in case['stages'])]]
            require([c['file'] for c in built['checkpoints']] == expected, f'native {case["name"]}: checkpoint inventory')
            for checkpoint in built['checkpoints']:
                path = directory / checkpoint['file']
                require(checkpoint['identity'] == dao.identity(path), 'retained checkpoint identity: ' + path.name)
                self.images[path.name] = path
            return result['environment']

        environments = self.parallel(build, self.cases)
        require(all(e == environments[0] for e in environments), 'native jobs ran under different providers')
        self.report['environment'] = environments[0]
        return {'cases': len(self.cases), 'checkpoints': len(self.images)}

    # Rust candidates
    def candidates(self):
        directory = self.out / 'candidates'
        logs = directory / 'logs'
        logs.mkdir(parents=True)

        def prepare(case):
            source = self.stage_path('native', case, 'original')
            path = directory / f'candidate-{case["name"]}-original.mdb'
            shutil.copyfile(source, path)
            images, requests = [path], 0
            for stage in case['stages']:
                target = directory / f'candidate-{case["name"]}-{stage["name"]}.mdb'
                shutil.copyfile(path, target)
                for number, op in enumerate(stage['operations']):
                    log = logs / f'{case["name"]}-{stage["name"]}-{number:03d}'
                    done = self.cli(['mutate', target], log, self.request_for(target, op))
                    require(done.returncode == 0, f'{log.name}: jet3-cli returned {done.returncode}: {done.stderr[-300:]}')
                    requests += 1
                validation = self.cli(['validate', target], logs / f'{case["name"]}-{stage["name"]}-validate')
                require(validation.returncode == 0, f'{target.name}: validate returned {validation.returncode}')
                images.append(target)
                path = target
            return images, requests

        prepared = self.parallel(prepare, self.cases)
        for images, _ in prepared:
            self.images.update({p.name: p for p in images})
        return {'images': sum(len(i) for i, _ in prepared), 'requests': sum(r for _, r in prepared)}

    def rust_refusals(self):
        directory = self.out / 'refusals'
        (directory / 'logs').mkdir(parents=True)
        count = 0
        for case in self.cases:
            source = self.stage_path('native', case, 'original')
            for refusal in plan.refusals(case):
                target = directory / f'candidate-{case["name"]}-{refusal["name"]}.mdb'
                shutil.copyfile(source, target)
                request = self.request_for(target, refusal['request'])
                self.cli([refusal['command'], target], directory / 'logs' / target.stem, request)
                count += 1
        return {'refusals': count}

    # Native continuations and failures
    def native_edits(self):
        def edit(case):
            directory = self.out / 'edits' / case['name']
            staged = directory / 'staged'
            staged.mkdir(parents=True)
            steps = [plan.native_op(op) for op in case['continuation']]
            job = {'inputs': [], 'edits': [{'name': f'{role}-{case["name"]}-continued',
                                            'source': f'{role}-{case["name"]}-reused.mdb',
                                            'file': f'{role}-{case["name"]}-continued.mdb', 'steps': steps}
                                           for role in ROLES]}
            sources = [self.stage_path(role, case, 'reused') for role in ROLES]
            if self.refusals:
                job['edits'] += plan.failure_edits(case)
                sources.append(self.stage_path('native', case, 'original'))
            dao.write_json(directory / 'native.json', job)
            files = [directory / 'native.json']
            for source in sources:
                shutil.copyfile(source, staged / source.name)
                files.append(staged / source.name)
            self.context.vm.run(NATIVE, files, f'{self.name}-edits-{case["name"]}', directory)
            result = dao.read_json(directory / 'native-result.json')
            dao.check_environment(result['environment'])
            require([e['name'] for e in result['edits']] == [e['name'] for e in job['edits']], 'native edit inventory')
            images = {}
            for record in result['edits']:
                path = directory / record['file']
                require(record['after'] == dao.identity(path), 'retained edit identity: ' + path.name)
                if record['name'].endswith('-continued'):
                    require(len(record['steps']) == len(steps) and all(s['ok'] for s in record['steps']),
                            f'native continuation {record["name"]}: {record["steps"]}')
                images[path.name] = path
            return images, {e['name']: e for e in result['edits']}

        for images, edits in self.parallel(edit, self.cases):
            self.images.update(images)
            self.edits.update(edits)
        return {'edits': len(self.edits)}

    # DAO observation
    def observe(self):
        files = sorted(self.images.values(), key=lambda p: p.name)
        batches = max(1, min(self.context.parallel, len(files)))
        groups = [files[i::batches] for i in range(batches)]

        def batch(number):
            return self.context.vm.run(dao.ORACLE / 'Observe.ps1', groups[number], f'{self.name}-observe-{number}',
                                       self.out / 'observe' / f'batch-{number}')

        environment = None
        for outbox in self.parallel(batch, range(batches)):
            result = dao.read_json(outbox / 'observe-result.json')
            dao.check_environment(result['environment'])
            require(environment in (None, result['environment']), 'observer batches ran under different providers')
            environment = result['environment']
            for item in result['files']:
                self.observations[item['file']] = item
        require(set(self.observations) == {p.name for p in files}, 'observation inventory differs from the requested images')
        return {'files': len(files), 'batches': batches}

    # Comparison
    @staticmethod
    def check(pair, name, function):
        try:
            pair['checks'][name] = {'status': 'pass', 'detail': function()}
        except Exception as error:
            pair['checks'][name] = {'status': 'fail', 'error': f'{type(error).__name__}: {error}'}

    def compare_all(self):
        raw_dir = self.out / 'compare'
        raw_dir.mkdir(exist_ok=True)
        pairs, totals = [], {'unrelated_page_comparisons': 0, 'unassigned_row_comparisons': 0}
        for case in self.cases:
            pairs += self.lifecycle_pairs(case, raw_dir, totals)
            if self.refusals:
                pairs += self.refusal_pairs(case)
        for pair in pairs:
            pair['status'] = 'pass' if pair['checks'] and all(c['status'] == 'pass' for c in pair['checks'].values()) else 'fail'
        counts = {}
        for pair in pairs:
            key = f'{pair["kind"]}/{pair["status"]}'
            counts[key] = counts.get(key, 0) + 1
        self.report.update(pairs=pairs, counts=counts, totals=totals)
        failed = [p['name'] for p in pairs if p['status'] == 'fail']
        require(not failed, f'{len(failed)} failed pairs: {failed[:5]}')
        expected = self.spec.get('expected_counts')
        require(expected is None or counts == expected, f'pair counts {counts} differ from the accepted {expected}')
        return {'pairs': len(pairs)}

    def lifecycle_pairs(self, case, raw_dir: Path, totals: dict) -> list[dict]:
        baseline_path = self.stage_path('native', case, 'original')
        baseline_bytes = baseline_path.read_bytes()
        baseline = structure.observe(baseline_path)
        baseline_view = self.observations[baseline_path.name]
        model = {next(iter(r[0].values())): r for r in case['initial']}
        previous = {role: baseline for role in ROLES}
        storages, pairs = {}, []
        stages = [{'name': 'original', 'operations': []}, *case['stages'],
                  {'name': 'continued', 'operations': case['continuation']}]
        for stage in stages:
            plan.apply_model(model, stage['operations'])
            expected_raw = [plan.model_row(case['columns'], r) for _, r in sorted(model.items())]
            expected_dao = [plan.model_row(case['columns'], r, dao=True) for _, r in sorted(model.items())]
            label = f'{case["name"]}-{stage["name"]}'
            pair = {'name': label, 'kind': 'lifecycle', 'checks': {}}
            raws, views = {}, {}
            for role in ROLES:
                def raw_check(role=role):
                    path = self.stage_path(role, case, stage['name'])
                    before, previous[role] = previous[role], None
                    observed = structure.observe(path)
                    previous[role] = observed
                    dao.write_json(raw_dir / f'{role}-{label}.json', observed)
                    validation = self.cli(['validate', path], raw_dir / f'{role}-{label}-validate')
                    require(validation.returncode == 0, f'strict Rust validation of {path.name}: {validation.stderr[-300:]}')
                    values = sorted((r['values'] for r in observed['tables']['Items']['rows']), key=lambda r: r['Id'])
                    require(values == expected_raw, 'complete raw values: ' + path.name)
                    pages = preserve(baseline, observed, baseline_bytes, path.read_bytes())
                    require(before is not None, 'previous stage image was not observed')
                    rows = surviving_rows(before, observed, stage['operations'])
                    raws[role] = observed
                    storages[stage['name'], role] = storage(observed)
                    totals['unrelated_page_comparisons'] += len(pages)
                    totals['unassigned_row_comparisons'] += len(rows)
                    summary = storages[stage['name'], role]
                    return {'unrelated_pages': len(pages), 'unassigned_rows': len(rows), 'pages': summary['pages'],
                            'free_pages': len(summary['free_pages']), 'size': summary['size'],
                            'overflow_rows': summary['overflow_rows']}

                def dao_check(role=role):
                    path = self.stage_path(role, case, stage['name'])
                    view = self.observations[path.name]
                    readback(view, baseline_view, expected_dao, path)
                    views[role] = normalized(view)

                self.check(pair, f'{role}/structure', raw_check)
                self.check(pair, f'{role}/readback', dao_check)
            if stage['name'] == 'original':
                self.check(pair, 'queries', lambda: require(
                    {q['name']: q['type'] for q in baseline_view['querydefs']} == {n: t for n, _, t in plan.QUERIES},
                    'complete query form inventory'))
            if len(raws) == 2:
                def semantic():
                    name = {'name': label, 'steps': []}
                    found = compare.differences(compare.semantics(raws['candidate'], name),
                                                compare.semantics(raws['native'], name))
                    require(not found, 'raw semantic mismatch: ' + str(compare.brief(found)))
                    # Placement may differ (EXP-0311); it is recorded, not required.
                    a, b = storages[stage['name'], 'candidate'], storages[stage['name'], 'native']
                    return {'same_maps': a['maps'] == b['maps'], 'same_pages': a['pages'] == b['pages'],
                            'same_size': a['size'] == b['size']}
                self.check(pair, 'semantics', semantic)
            if len(views) == 2:
                def snapshot():
                    found = compare.differences(views['candidate'], views['native'])
                    require(not found, 'DAO snapshot mismatch: ' + str(compare.brief(found)))
                self.check(pair, 'snapshot', snapshot)
            if stage['name'] == 'reused':
                for role in ROLES:
                    self.check(pair, f'{role}/reuse',
                               lambda role=role: reuse(storages['released', role], storages['reused', role]))
            pairs.append(pair)
        return pairs

    def refusal_pairs(self, case) -> list[dict]:
        source = self.stage_path('native', case, 'original')
        before = source.read_bytes()
        baseline = structure.observe(source)
        baseline_view = self.observations[source.name]
        expected_rows = [plan.model_row(case['columns'], r, dao=True) for r in case['initial']]
        pairs = []
        for refusal in plan.refusals(case):
            label = f'{case["name"]}-{refusal["name"]}'
            relationship = refusal['name'] == 'relationship'
            pair = {'name': label, 'kind': 'refusal', 'checks': {}}

            def rust():
                target = self.out / 'refusals' / f'candidate-{label}.mdb'
                result = dao.read_json(self.out / 'refusals' / 'logs' / f'candidate-{label}.result.json')
                require(result['returncode'] == 1 and target.read_bytes() == before, 'whole-file Rust refusal: ' + label)
                error = json.loads(result['stderr'])
                reason = {'duplicate': 'duplicate unique key', 'required': 'RequiredValueMissing',
                          'relationship': 'RelationshipConstraint'}[refusal['name']]
                boundary = case['kind'] == 'sparse' and relationship
                if boundary:
                    # The native orphan control has an AutoNumber child, which Rust refuses by type.
                    reason = 'Unsupported("relationship column types")'
                require(reason in error['message'] and error['publication_stage'] == ('Mutation' if relationship else None),
                        'Rust refusal reason/stage: ' + label)
                return {'comparison': 'rust_support_boundary' if boundary else 'matching_constraint_refusal',
                        'message': error['message'], 'publication_stage': error['publication_stage']}

            def native(suffix):
                edit = self.edits[f'failure-{label}{suffix}']
                path = self.image(f'failure-{label}{suffix}.mdb')
                data = path.read_bytes()
                require(edit['before'] == dao.identity(source) and edit['after'] == dao.identity(path), 'native identities')
                *completed, failed = edit['steps']
                require(all(s['ok'] for s in completed) and not failed['ok'] and failed['error']['numbers'] == [refusal['error']],
                        f'exact DAO refusal {refusal["error"]}: {[s.get("error") for s in edit["steps"]]}')
                view = self.observations[path.name]
                readback(view, baseline_view, expected_rows, path)
                validation = self.cli(['validate', path], self.out / 'compare' / f'failure-{label}{suffix}-validate')
                expected = native_failure_bytes(before, baseline, case['kind'], refusal['name'])
                require(data == expected, 'exact native failure/rollback bookkeeping: ' + label + suffix)
                require(validation.returncode == (1 if relationship else 0), 'strict native validation result: ' + label + suffix)
                raw = structure.observe(path, {'MSysObjects': 1, 'MSysACEs': 2} if relationship else None)
                name = {'name': label, 'steps': []}
                raw_diff = compare.differences(compare.semantics(baseline, name), compare.semantics(raw, name))
                found = compare.differences(normalized(baseline_view), normalized(view))
                require(not found, 'complete native failure snapshot: ' + str(compare.brief(found)))
                return {'rollback': bool(suffix), 'validation_returncode': validation.returncode,
                        'exact_bytes': changed_bytes(before, data), 'raw_differences': compare.brief(raw_diff, 20)}

            self.check(pair, 'rust', rust)
            self.check(pair, 'native', lambda: native(''))
            self.check(pair, 'native-rollback', lambda: native('-rollback'))
            pairs.append(pair)
        return pairs

    def run(self):
        dao.write_json(self.out / 'spec.json', self.spec)
        try:
            self.stage('native', self.native)
            self.stage('candidates', self.candidates)
            if self.refusals:
                self.stage('refusals', self.rust_refusals)
            self.stage('edits', self.native_edits)
            self.stage('observe', self.observe)
            self.stage('compare', self.compare_all)
            self.report['outcome'] = 'matched'
        except dao.Stage:
            pass
        finally:
            dao.write_json(dao.new_file(self.out / 'report.json'), self.report)
        return self.report


def run(name, spec, out, context):
    return StorageRun(name, spec, out, context).run()
