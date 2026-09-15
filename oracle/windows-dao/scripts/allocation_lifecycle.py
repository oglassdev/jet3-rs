#!/usr/bin/env python3
"""Four bounded allocation lifecycles with native successors and retained-input edits."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import struct
import subprocess

from field_update import identity, canonical
import allocation_lifecycle_structure as raw

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'allocation-lifecycle.json'
GENERATOR = ROOT / 'target/debug/examples/allocation_candidate'
CONFIG = {'rows-inline': (1000, False), 'rows-slot': (16350, False),
          'payload-inline': (512, True), 'payload-slot': (8200, True)}
NOTES_FIELDS = [['Id', 4, 4, False], ['Body', 10, 64, False]]
require = raw.require


def write(path, value):
    path.write_text(canonical(value) + '\n')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def operations(base, old, deleted):
    return [dict(kind='insert', id=base, seed=base),
            dict(kind='replace', old=old, id=base + 1, seed=base + 1), dict(kind='delete', id=deleted)]


def recipe(name):
    count, payload = CONFIG[name]
    fields = ([['Id', 4, 4, False], ['Tag', 4, 4, False], ['Body', 12, 0, False], ['Blob', 11, 0, False]]
              if payload else [['Id', 4, 4, False]] + [[f'Pad{c}', 10, 255, True] for c in range(4)])
    indexes = [dict(name='ById', fields=[[0, False]], primary=True, unique=True, required=True,
                    queries=[[id] for id in (0, 2, 3, 4, 5, 6, count - 1, 100000, 100001, 200000, 200001, 300000, 300001, 400000, 400063, 400064, 999999)])]
    if payload:
        indexes.append(dict(name='ByTag', fields=[[1, True]], primary=False, unique=False, required=False,
                            queries=[[n] for n in range(-9, 10)]))
    return dict(name=name, initial_count=count, payload=payload, fields=fields, indexes=indexes,
                stages=[dict(name='original', operations=[]), dict(name='mutated', operations=operations(100000, 3, 2))],
                native=operations(200000, 4, 5))


def row_values(case, id, seed):
    result = [struct.pack('<i', id)]
    if case['payload']:
        result.extend([None if seed % 13 == 0 else struct.pack('<i', seed % 17 - 8),
                       None if seed == 0 else bytes([65 + seed % 26]) * 1800,
                       None if seed == 1 else bytes((n * 37 + seed) % 256 for n in range(1800))])
    else:
        result.extend(bytes([65 + (seed + 3 * c) % 26]) * 255 for c in range(4))
    return result


def apply(model, edits):
    for op in edits:
        if op['kind'] == 'delete':
            require(op['id'] in model, 'Model deletion exists'); del model[op['id']]
        else:
            if op['kind'] == 'replace':
                require(op['old'] in model, 'Model replacement exists'); del model[op['old']]
            require(op['id'] not in model, 'Model insert is unique'); model[op['id']] = op['seed']


def expected(case, model):
    return {id: row_values(case, id, seed) for id, seed in sorted(model.items())}


def stages(case):
    model = dict(enumerate(range(case['initial_count'])))
    for stage in case['stages']:
        apply(model, stage['operations']); yield stage, model.copy()


def inputs():
    names = ['allocation_lifecycle.py', 'allocation_lifecycle.ps1', 'allocation_lifecycle.cs',
             'allocation_lifecycle_structure.py', 'numeric_index_mutation_structure.py',
             'numeric_index_mutation.ps1', 'field_update.py', 'field_update.ps1',
             'system_catalog.py', 'index_tree_mutation_structure.py', 'multi_level_index_structure.py']
    paths = [SCRIPT.with_name(n) for n in names]
    paths += [ROOT / 'crates/jet3/examples/allocation_candidate.rs', ROOT / 'crates/jet3/examples/allocation_support/reader.rs']
    return {str(p.relative_to(ROOT)): identity(p) for p in paths}


def run(generator, args, root, label):
    result = subprocess.run([str(generator), *map(str, args)], cwd=ROOT, capture_output=True, text=True, timeout=900)
    (root / (label + '.stdout.log')).write_text(result.stdout)
    (root / (label + '.stderr.log')).write_text(result.stderr)
    require(result.returncode == 0, f'{label} failed ({result.returncode}); see retained logs in {root}')
    return result.stdout


def raw_check(path, case, model, receipt_path=None):
    receipt = read(receipt_path) if receipt_path else None
    return raw.inspect(path.read_bytes(), case, expected(case, model), receipt, receipt_path.parent if receipt_path else None)


def boundary(layout, case):
    minimum = 16352 if case['name'].endswith('slot') else 1024
    require(layout['pages'] > minimum, 'Actual file crosses allocation boundary: ' + case['name'])
    if minimum == 16352:
        global_map = layout['maps']['global']
        require(global_map['kind'] == 1 and len(global_map['references']) >= 2 and global_map['references'][1] != 0,
                'Global second bitmap slot is active')
        owned = ['lval2_owned', 'lval3_owned'] if case['payload'] else ['table_owned']
        for role in owned:
            require(any(end > minimum for _, end in layout['maps'][role]['members']), 'Owned pages cross bitmap slot: ' + role)


def rollback(directory, layouts, generator):
    definitions = [('outside-eof', 'rows-slot', 'global'), ('data-as-bitmap', 'payload-slot', 'lval2_owned'),
                   ('bitmap-alias', 'payload-slot', 'lval2_owned')]
    receipts = []
    for name, case_name, role in definitions:
        stem = 'refusal-' + name; source = directory / (case_name + '-mutated.mdb'); image = bytearray(source.read_bytes())
        layout = layouts[case_name]; mapping = layout['maps'][role]
        require(mapping['kind'] == 1 and mapping['references'][0], 'Rollback exercises an active indirect map')
        replacement = (len(image) // 2048 + 1 if name == 'outside-eof' else
                       layout['locators'][0][1] if name == 'data-as-bitmap' else layout['maps']['lval3_owned']['references'][0])
        require(replacement != mapping['references'][0] and replacement != 0, 'Rollback changes active reference')
        locator = mapping['locator']; page = raw.catalog._page(image, locator['page'], 'rollback map')
        entry = next(e for e in raw.catalog._row_directory(page, locator['page']) if e['row'] == locator['row'])
        offset = locator['page'] * 2048 + entry['start'] + 1
        image[offset:offset + 4] = replacement.to_bytes(4, 'little')
        before = directory / (stem + '-before.mdb'); after = directory / (stem + '-after.mdb'); before.write_bytes(image)
        result = json.loads(run(generator, ['refuse', before, after, case_name], directory, stem))
        require(result['preserved'] and before.read_bytes() == after.read_bytes(), 'Whole damaged image preserved')
        receipts.append(dict(name=name, source=identity(source), before=identity(before), after=identity(after),
                             offset=offset, old_reference=mapping['references'][0], replacement=replacement, result=result))
    write(directory / 'refusals.json', receipts)
    return receipts


def verify_refusals(directory, manifest):
    require(read(directory / 'refusals.json') == manifest['refusals'] and
            [r['name'] for r in manifest['refusals']] == ['outside-eof', 'data-as-bitmap', 'bitmap-alias'], 'Rollback receipt inventory')
    for receipt in manifest['refusals']:
        stem = 'refusal-' + receipt['name']
        require(identity(directory / (stem + '-before.mdb')) == receipt['before'] == receipt['after'] ==
                identity(directory / (stem + '-after.mdb')) and receipt['result']['preserved'], 'Retained whole-image rollback')


def stage_helpers(directory):
    for name in ('allocation_lifecycle.ps1', 'allocation_lifecycle.cs', 'numeric_index_mutation.ps1'):
        shutil.copy2(SCRIPT.with_name(name), directory / name)


def manifest_files(directory):
    return {p.name: identity(p) for p in sorted(directory.iterdir()) if p.is_file() and
            p.suffix in ('.mdb', '.json', '.bin', '.log') and p.name != MANIFEST}


def prepare(candidates, revision, generator=GENERATOR):
    cases = [recipe(name) for name in CONFIG]; layouts = {}
    for case in cases:
        notes = None
        for stage, model in stages(case):
            stem = case['name'] + '-' + stage['name']
            layout = raw_check(candidates / (stem + '.mdb'), case, model, candidates / (stem + '.snapshot.json'))
            boundary(layout, case)
            if notes is None: notes = layout['notes']
            require(layout['notes'] == notes, 'Unrelated Notes pages preserved: ' + stem)
            layouts[case['name']] = layout
        case['notes_pages'] = notes
    refusals = rollback(candidates, layouts, generator)
    manifest = dict(document_type='allocation_lifecycle_inputs', round='mutations', source_revision=revision,
                    cases=cases, files=manifest_files(candidates), inputs=inputs(), refusals=refusals)
    write(candidates / MANIFEST, manifest); stage_helpers(candidates)
    return manifest


def normalized(capture, case, rows):
    require(capture['status'] == 'pass' and capture['error'] is None, 'DAO capture completed')
    value = copy.deepcopy(capture['snapshot'])
    require(value['version'] == '3.0' and value['tables'] == ['Items', 'MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Notes'] and
            value['queries'] == value['relations'] == [], 'Complete DAO database inventory')
    require([t['name'] for t in value['schema']] == ['Items', 'Notes'], 'Complete DAO user schema inventory')
    items, notes = value['schema']
    for table, fields in ((items, case['fields']), (notes, NOTES_FIELDS)):
        require(table['attributes'] == 0 and [[f['name'], f['type'], f['size']] for f in table['fields']] ==
                [f[:3] for f in fields], 'Complete DAO field names/types/sizes')
        for field, spec in zip(table['fields'], fields):
            attrs = 1 if spec[1] == 4 or spec[3] else 2
            require(field['attributes'] == attrs and field['required'] is False and field['allow_zero_length'] is False and
                    field['default_value'] == '', 'Complete DAO field properties: ' + field['name'])
    indexes = [dict(name=i['name'], primary=i['primary'], unique=i['unique'], required=i['required'], ignore_nulls=False,
                    foreign=False, fields=[dict(name=case['fields'][c][0], attributes=int(d)) for c, d in i['fields']]) for i in case['indexes']]
    require(sorted(items['indexes'], key=lambda i: i['name']) == sorted(indexes, key=lambda i: i['name']) and notes['indexes'] == [], 'Complete DAO indexes')
    require(value['count'] == len(rows) and value['digest'] == raw.canonical_digest(rows[id] for id in sorted(rows)) and
            value['notes'] == [[7, 'allocation-control']], 'Complete DAO row/payload digest and unrelated Notes')
    require(set(value['index_reads']) == {i['name'] for i in case['indexes']}, 'Complete DAO traversal inventory')
    for index in case['indexes']:
        actual = value['index_reads'][index['name']]; ids = actual['ids']; keys = {id: raw.key(row, index) for id, row in rows.items()}
        require(sorted(ids) == sorted(rows) and [keys[id] for id in ids] == sorted(keys.values()), 'Every directed DAO index entry')
        require([s['query'] for s in actual['seek']] == index['queries'], 'Complete full-key Seek inventory')
        for seek in actual['seek']:
            query_row = [None] * len(case['fields'])
            for (column, _), query in zip(index['fields'], seek['query']): query_row[column] = struct.pack('<i', query)
            wanted = raw.key(query_row, index); matches = sorted(id for id, key in keys.items() if key == wanted)
            require((seek['id'] in matches and seek['digest'] == raw.canonical_digest([rows[seek['id']]])) if matches else
                    seek['id'] is None and seek['digest'] is None, 'Seek returns complete matching row/payload or absence')
            seek['matches'] = matches; del seek['id']; del seek['digest']
        actual['ids'] = sorted(ids, key=lambda id: (keys[id], id))
    items['indexes'].sort(key=lambda i: i['name'])
    return value


def retained_capture(outbox, capture, case, model, notes, generator, receipt_path=None):
    path = outbox / capture['file']; require(path.name == capture['file'], 'Capture filename')
    image = identity(path)
    require(capture['before'] == capture['after'] == image, 'Read-only closed capture image identity')
    normalized_snapshot = normalized(capture, case, expected(case, model))
    if receipt_path is None:
        receipt_path = outbox / (path.stem + '.reader.json')
        run(generator, ['inspect', path, receipt_path], outbox, path.stem + '-reader')
    layout = raw_check(path, case, model, receipt_path)
    require(layout['notes'] == notes, 'Every unrelated Notes page preserved')
    boundary(layout, case)
    return normalized_snapshot, dict(image=image, layout=layout, reader=identity(receipt_path))


def aggregate(outbox):
    index_path = outbox / 'allocation-workers.json'; workers = read(index_path)
    require(workers['document_type'] == 'dao_allocation_lifecycle_workers' and
            [w['name'] for w in workers['workers']] == list(CONFIG), 'Fresh worker inventory')
    combined = None
    for worker in workers['workers']:
        require(worker['file'] == worker['name'] + '-result.json', 'Worker filename')
        path = outbox / worker['file']; require(identity(path) == worker['image'], 'Worker result identity')
        result = read(path)
        require(result['document_type'] == 'dao_allocation_lifecycle_mutation_result' and
                all(result[k] == workers[k] for k in ('source_revision', 'manifest_sha256', 'round')) and
                [c['name'] for c in result['cases']] == [worker['name']], 'One bound worker result')
        if combined is None:
            combined = dict(result, cases=[], retention_failures=[], error=None, worker_index=identity(index_path), worker_results=[])
        require(result['environment'] == combined['environment'], 'Identical native provider environment')
        combined['cases'].extend(result['cases']); combined['retention_failures'].extend(result['retention_failures'])
        combined['worker_results'].append(worker)
        if result['error'] is not None or worker['exit_code'] != 0: combined['error'] = 'Native worker failed; see retained worker result'
    path = outbox / 'result.json'
    if path.exists(): require(read(path) == combined, 'Existing aggregate is identical')
    else: write(path, combined)


def retain_report(outbox, report):
    path = outbox / 'allocation-lifecycle-report.json'; suffix = 1
    while path.exists() and read(path) != report:
        suffix += 1; path = outbox / f'allocation-lifecycle-report-{suffix}.json'
    write(path, report)


def evaluate(candidates, outbox, generator=GENERATOR):
    manifest_path = candidates / MANIFEST; manifest = read(manifest_path); result = read(outbox / 'result.json')
    report = dict(document_type='dao_allocation_lifecycle_report', status='failed', round=manifest['round'],
                  source_revision=manifest['source_revision'], manifest=identity(manifest_path), result=identity(outbox / 'result.json'), cases=[], error=None)
    try:
        require(manifest['inputs'] == inputs(), 'Current harness inputs')
        require(identity(outbox / MANIFEST) == report['manifest'], 'Retained manifest identity')
        for name, pin in manifest['files'].items(): require(identity(candidates / name) == identity(outbox / name) == pin, 'Retained input: ' + name)
        require(result['document_type'] == 'dao_allocation_lifecycle_mutation_result' and result['manifest_sha256'] == report['manifest']['sha256'] and
                result['source_revision'] == manifest['source_revision'] and result['round'] == manifest['round'], 'Native source revision/manifest/round')
        require(result['error'] is None and result['retention_failures'] == [], 'Native producer and retention completed')
        environment = result['environment']; report['environment'] = environment
        require(environment['process_bits'] == 32 and environment['provider'] == 'DAO.DBEngine.36' and
                environment['provider_version'] == '3.6' and len(environment['dll']['sha256']) == 64, 'Actual loaded native provider')
        require([c['name'] for c in result['cases']] == list(CONFIG) == [c['name'] for c in manifest['cases']], 'Case inventory')
        for case, observed in zip(manifest['cases'], result['cases']):
            outcome = dict(name=case['name'], status='failed', checkpoints=[], error=None); report['cases'].append(outcome)
            try:
                require(observed['status'] == 'pass' and observed['error'] is None, 'Native case completed')
                if manifest['round'] == 'continuation':
                    model = {int(id): seed for id, seed in case['expected_model']}; pairs = {}; details = {}
                    require(observed['operation']['count'] == len(case['operations']) and
                            observed['operation']['before'] == manifest['files'][case['source_file']], 'Continuation source binding')
                    for role in ('candidate', 'control'):
                        receipt = candidates / (case['name'] + '-continued.snapshot.json') if role == 'candidate' else None
                        pairs[role], details[role] = retained_capture(outbox, observed['roles'][role], case, model, case['notes_pages'], generator, receipt)
                    require(details['candidate']['image'] == manifest['files'][case['candidate_file']] and
                            details['control']['image'] == observed['operation']['after'], 'Continuation output binding')
                    require(pairs['candidate'] == pairs['control'], 'Full native-input continuation comparison')
                    outcome['checkpoints'].append(dict(name='continued', roles=details))
                else:
                    require([s['name'] for s in observed['stages']] == [s['name'] for s in case['stages']], 'Native checkpoint inventory')
                    created = outbox / observed['created']['file']; chain = identity(created)
                    require(chain == observed['created']['image'], 'Native creation image')
                    initial = dict(enumerate(range(case['initial_count'])))
                    native_notes = raw_check(created, case, initial)['notes']
                    for (stage, model), checkpoint in zip(stages(case), observed['stages']):
                        require(checkpoint['operations'] == stage['operations'] and checkpoint['mutation']['before'] == chain and
                                checkpoint['mutation']['count'] == len(stage['operations']), 'Native operation chain')
                        pairs = {}; details = {}; stem = case['name'] + '-' + stage['name']
                        for role in ('candidate', 'control'):
                            receipt = candidates / (stem + '.snapshot.json') if role == 'candidate' else None
                            notes = case['notes_pages'] if role == 'candidate' else native_notes
                            pairs[role], details[role] = retained_capture(outbox, checkpoint['roles'][role], case, model, notes, generator, receipt)
                        require(details['candidate']['image'] == manifest['files'][stem + '.mdb'] and
                                details['control']['image'] == checkpoint['mutation']['after'], 'Checkpoint output binding')
                        require(pairs['candidate'] == pairs['control'], 'Complete paired DAO schema/rows/traversal/Seek')
                        chain = details['control']['image']; outcome['checkpoints'].append(dict(name=stage['name'], roles=details))
                    apply(model, case['native']); pairs = {}; native_details = {}
                    for role in ('candidate', 'control'):
                        native = observed['native'][role]
                        require(native['mutation']['before'] == details[role]['image'] and native['mutation']['count'] == len(case['native']), 'Native successor source binding')
                        notes = case['notes_pages'] if role == 'candidate' else native_notes
                        pairs[role], native_details[role] = retained_capture(outbox, native['capture'], case, model, notes, generator)
                        require(native['mutation']['after'] == native_details[role]['image'], 'Native successor output binding')
                    require(pairs['candidate'] == pairs['control'], 'Complete native successor pair')
                    outcome['native'] = native_details
                outcome['status'] = 'accepted'
            except Exception as error: outcome['error'] = str(error)
        if manifest['round'] == 'mutations': verify_refusals(candidates, manifest); report['refusals'] = manifest['refusals']
        require(all(c['status'] == 'accepted' for c in report['cases']), 'One or more allocation cases failed')
        report['status'] = 'accepted'
    except Exception as error: report['error'] = str(error)
    finally: retain_report(outbox, report)
    return report


def prepare_continue(candidates, first_outbox, output, generator, revision):
    first = read(candidates / MANIFEST); result = read(first_outbox / 'result.json')
    require(result['manifest_sha256'] == identity(candidates / MANIFEST)['sha256'] and result['source_revision'] == first['source_revision'] and
            result['round'] == 'mutations' and result['error'] is None, 'Continuation parent binding')
    output.mkdir(parents=True, exist_ok=False); cases = []; receipts = []
    try:
        for case, observed in zip(first['cases'], result['cases']):
            case = copy.deepcopy(case); require(case['name'] == observed['name'] and observed['status'] == 'pass', 'Parent case')
            _, model = list(stages(case))[-1]; apply(model, case['native'])
            capture = observed['native']['control']['capture']; source = first_outbox / capture['file']
            require(identity(source) == capture['before'] == capture['after'], 'Closed native continuation input')
            normalized(capture, case, expected(case, model)); layout = raw_check(source, case, model); boundary(layout, case)
            edits = operations(300000, 6, 200000)
            if case['name'] == 'rows-inline':
                edits += [dict(kind='insert', id=id, seed=id) for id in range(400000, 400064)]
            apply(model, edits)
            child = output / case['name']; run(generator, ['continue', source, child, case['name']], output, case['name'])
            source_name = case['name'] + '-continuation-source.mdb'; shutil.copy2(source, output / source_name)
            for path in child.iterdir(): shutil.copy2(path, output / path.name)
            stem = case['name'] + '-continued'; changed = raw_check(output / (stem + '.mdb'), case, model, output / (stem + '.snapshot.json'))
            require(changed['notes'] == layout['notes'], 'Native-input continuation preserves Notes pages')
            if case['name'] == 'rows-inline':
                for role in ('global', 'table_owned'):
                    require(layout['maps'][role]['kind'] == 0 and layout['maps'][role]['length'] > 133 and
                            changed['maps'][role]['kind'] == 1 and changed['maps'][role]['length'] == 133,
                            'Native widened inline map converts to compact indirect row: ' + role)
            case.update(source_file=source_name, candidate_file=stem + '.mdb', expected_model=sorted(model.items()),
                        operations=edits, notes_pages=layout['notes'])
            cases.append(case); receipts.append(dict(name=case['name'], source=identity(source), output=identity(output / (stem + '.mdb'))))
        manifest = dict(document_type='allocation_lifecycle_inputs', round='continuation', source_revision=revision, cases=cases,
                        files=manifest_files(output), inputs=inputs(), parent_manifest=identity(candidates / MANIFEST), parent_result=identity(first_outbox / 'result.json'))
        write(output / MANIFEST, manifest); stage_helpers(output)
    finally: write(output / 'continuation-preparation.json', receipts)


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare'); prepare_parser.add_argument('candidates', type=Path); prepare_parser.add_argument('revision')
    evaluate_parser = sub.add_parser('evaluate'); evaluate_parser.add_argument('candidates', type=Path); evaluate_parser.add_argument('outbox', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare': prepare(args.candidates, args.revision)
    else: return int(evaluate(args.candidates, args.outbox)['status'] != 'accepted')


if __name__ == '__main__': raise SystemExit(main())
