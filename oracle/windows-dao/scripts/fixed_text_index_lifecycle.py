#!/usr/bin/env python3
"""Fixed Text index creation, mutations, native successors and native inputs."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

import wide_row_lifecycle as wide
from index_tree_mutation import identity, notes_identity, require, write

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'fixed-text-index-lifecycle.json'
WIDTHS = dict(fixed1=1, fixed8=8, fixed32=32, fixed255=255)
engine = wide.engine
original_inputs = engine.inputs


def code(name, seed):
    if name == 'fixed8':
        return {1:b'a       ', 100:b'c       ', 101:b'd       '}.get(seed, f'{seed:08}'.encode()).hex()
    if seed % 11 == 0:
        return ((b'A' if (seed // 11) % 2 == 0 else b'a') + b' ' * (WIDTHS[name] - 1)).hex()
    return wide.payload(WIDTHS[name], seed, 1, True)


def row(name, id):
    return [id, None if id % 13 == 0 else code(name, id), None if id % 7 == 0 else id % 5,
            wide.payload(80, id, 3, True), wide.payload(80, id, 4, False)]


def recipe(name):
    fields = [['Id',4,4], ['Code',10,WIDTHS[name]], ['Group',4,4], ['Body',12,0], ['Blob',11,0]]
    indexes = [dict(name=n, fields=f, primary=n=='ById', unique=n=='ById' or (name=='fixed8' and n=='ByCode'), required=n=='ById', ignore=n=='ByCodeDesc')
               for n,f in [('ById',[[0,False]]), ('ByCode',[[1,False]]), ('ByCodeDesc',[[1,True]]), ('ByPair',[[1,True],[2,False]])]]
    insert = lambda id: dict(kind='insert', row=row(name,id))
    clear = row(name,2); clear[1] = None
    present = row(name,13); present[1] = code(name,101)
    stages = [dict(name='original', operations=[]),
              dict(name='edited', operations=[dict(kind='field',id=1,column=1,value=code(name,100)),
                   dict(kind='replace',id=2,row=clear), dict(kind='replace',id=13,row=present),
                   dict(kind='field',id=7,column=0,value=700), dict(kind='delete',id=3)] + [insert(id) for id in range(1000,1012)]),
              dict(name='regrown', operations=[dict(kind='delete',id=id) for id in range(20,60)] + [insert(id) for id in range(2000,2040)])]
    native = [insert(9000),dict(kind='field',id=9000,column=0,value=9001),dict(kind='delete',id=1000)]
    samples = [row(name,id) for id in [*range(96), *range(1000,1012), *range(2000,2040), 9000,9001,1234567,1234568,7777]]
    samples += [clear,present,[1,code(name,100),1,None,None]]
    for index in indexes:
        queries = [[r[c] for c,_ in index['fields']] for r in samples]
        index['queries'] = list({json.dumps(q):q for q in queries if all(v is not None for v in q)}.values())
    collision = b'A       '.hex()
    inserted = row(name,9999); inserted[1] = collision
    replaced = row(name,2); replaced[1] = collision
    refusals = [dict(name='duplicate-insert', operation=dict(kind='insert',row=inserted)),
                dict(name='duplicate-field', operation=dict(kind='field',id=2,column=1,value=collision)),
                dict(name='duplicate-row', operation=dict(kind='replace',id=2,row=replaced))] if name=='fixed8' else []
    return dict(name=name, fields=fields, fixed_fields=['Code','Group'], indexes=indexes,
                initial_rows=[row(name,id) for id in range(96)], stages=stages, native=native, refusals=refusals)


def refusal_check(directory, notes):
    receipts = json.loads((directory/'refusals.json').read_text())
    require([r['name'] for r in receipts] == ['width','duplicate-insert','duplicate-field','duplicate-row','resource'], 'Refusal inventory')
    source = (directory/'fixed8-original.mdb').read_bytes()
    errors = dict(width='Encoding(InvalidWidth { ordinal: 1, physical_type: Text, expected: 8, actual: 7 })',
                  resource='Open(Header(Read(ResourceLimitExceeded { kind: TotalWorkUnits, requested: 1, maximum: 0 })))')
    errors.update({f'duplicate-{kind}':'Unsupported("duplicate unique key")' for kind in ('insert','field','row')})
    for receipt in receipts:
        name = receipt['name']
        require((directory/f'refusal-{name}-before.mdb').read_bytes() ==
                (directory/f'refusal-{name}-after.mdb').read_bytes() == source and receipt['preserved'],
                'Whole-image refusal preservation')
        require(receipt['error'] == errors[name], 'Declared refusal reason')
        require(notes_identity(source) == notes, 'Refusal Notes preservation')
    return receipts


def inputs():
    paths = [Path(__file__), SCRIPT, ROOT/'crates/jet3/examples/fixed_text_index_candidate.rs']
    return original_inputs() | {str(p.relative_to(ROOT)):identity(p) for p in paths}


def stage_helpers(directory):
    for name in ('numeric_index_mutation.ps1','field_update.ps1'):
        shutil.copy2(SCRIPT.with_name(name),directory/name)


def evaluate(directory, outbox):
    engine.aggregate(outbox)
    semantic = engine.evaluate(directory,outbox)
    manifest = json.loads((directory/MANIFEST).read_text())
    result = json.loads((outbox/'result.json').read_text())
    report = dict(document_type='dao_fixed_text_lifecycle_report', status='failed',
                  source_revision=semantic['source_revision'], round=manifest['round'],
                  semantic_status=semantic['status'], refusals=[], error=None)
    try:
        require(semantic['status']=='accepted', 'Complete lifecycle comparisons')
        for case, observed in zip(manifest['cases'],result['cases']):
            expected = case['refusals'] if manifest['round']=='mutations' else []
            require([r['name'] for r in observed['refusals']]==[r['name'] for r in expected], 'Native refusal inventory')
            rows = {r[0]:r for r in case['initial_rows']}
            for request, attempt in zip(expected,observed['refusals']):
                require(attempt['operation']==request['operation'], 'Exact native refusal operation')
                details = {}; pairs = {}; counters = {}
                for role in ('candidate','control'):
                    refused = attempt['roles'][role]
                    source_capture = observed['stages'][0]['roles'][role]
                    source = outbox/source_capture['file']; before = source.read_bytes()
                    require(refused['before']==identity(source), 'Refusal starts from original checkpoint')
                    require(refused['error'] is not None and refused['numbers']==[3022], 'DAO duplicate-key error 3022')
                    # Failed DAO writes may advance historical counters; compare both outputs exactly.
                    data = (outbox/refused['capture']['file']).read_bytes()
                    table = wide.base.tables(data)['Items']
                    counters[role] = {i['name']:table['physical_indexes'][i['physical_index']]['entry_count'] for i in table['logical_indexes']}
                    pairs[role], details[role], _ = engine.retained_capture(
                        outbox,refused['capture'],case,rows,counters[role],notes_identity(before))
                    require(pairs[role]==engine.normalized(source_capture,case,rows), 'Refusal preserves complete schema, rows, traversals and Seeks')
                    details[role].update(before=refused['before'], numbers=refused['numbers'],
                                         byte_identical=before==data, counters=counters[role])
                require(pairs['candidate']==pairs['control'] and counters['candidate']==counters['control'], 'Paired refusal semantics and historical counters')
                report['refusals'].append(dict(name=request['name'],roles=details))
        report['status'] = 'accepted'
    except Exception as error: report['error'] = str(error)
    path = outbox/'fixed-text-lifecycle-report.json'; suffix = 1
    while path.exists() and json.loads(path.read_text())!=report:
        suffix += 1; path = outbox/f'fixed-text-lifecycle-report-{suffix}.json'
    write(path,report); print(path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('prepare'); p.add_argument('candidates',type=Path); p.add_argument('revision')
    p = sub.add_parser('evaluate'); p.add_argument('candidates',type=Path); p.add_argument('outbox',type=Path)
    p = sub.add_parser('prepare-continue'); p.add_argument('candidates',type=Path); p.add_argument('outbox',type=Path)
    p.add_argument('output',type=Path); p.add_argument('generator',type=Path); p.add_argument('revision')
    args = parser.parse_args()
    if args.command == 'evaluate':
        return int(evaluate(args.candidates,args.outbox)['status'] != 'accepted')
    revision = subprocess.check_output(['git','rev-parse',args.revision],cwd=ROOT,text=True).strip()
    if args.command == 'prepare':
        engine.prepare(args.candidates,revision); stage_helpers(args.candidates)
    else:
        engine.prepare_continue(args.candidates,args.outbox,args.output,args.generator,revision)
        stage_helpers(args.output)


engine.CASE_NAMES = tuple(WIDTHS)
engine.SCRIPT = SCRIPT
engine.MANIFEST = MANIFEST
engine.recipe = recipe
engine.initial_row = row
engine.inputs = inputs
engine.refusal_check = refusal_check
if __name__ == '__main__': raise SystemExit(main())
