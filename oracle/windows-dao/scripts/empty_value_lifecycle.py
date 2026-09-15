#!/usr/bin/env python3
"""Per-column empty Text/Memo options, empty OLE normalization and native continuations."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import fixed_text_index_lifecycle as fixed
from index_tree_mutation import identity, notes_identity, require, write

wide = fixed.wide
engine = wide.engine
ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'empty-value-lifecycle.json'
CASES = ('mixed-first', 'mixed-later', 'property-chain', 'unique-text')
original_apply = engine.apply


def row(name, id):
    code = None if id % 13 == 0 else '' if id == 1 or (name != 'unique-text' and id % 7 == 1) else f'{id:08}'.encode().hex()
    length = [0, 0, 1, 33, 4096][id % 5]
    result = [id, code, b'        '.hex(), None if id % 5 == 0 else wide.payload(length, id, 3, True),
              b'kept'.hex(), wide.payload(32, id, 5, True), None if id % 5 == 0 else wide.payload(length, id, 6, False), id % 5, '']
    if name == 'property-chain': result += ['' if i % 2 == 0 else b'value'.hex() for i in range(26)]
    return result


def normalize(row):
    result = row.copy()
    if result[6] == '': result[6] = None
    return result


def apply(rows, operation, case, counters):
    original_apply(rows, operation, case, counters)
    for id in rows: rows[id] = normalize(rows[id])


def recipe(name):
    fields = [['Id',4,4], ['Code',10,8], ['Fixed',10,8], ['Body Memo',12,0], ['OtherText',10,8],
              ['OtherMemo',12,0], ['Blob',11,0], ['Group',4,4], ['Extra_Memo',12,0]]
    allow = ['Code', 'Body Memo', 'Extra_Memo']
    if name == 'property-chain':
        names = [f'Field{i:02}_' + 'x' * 56 for i in range(26)]
        fields += [[n,10,8] for n in names]; allow += names[::2]
    indexes = [dict(name=n, fields=f, primary=n=='ById', unique=n=='ById' or (name=='unique-text' and n=='ByCode'), required=n=='ById', ignore=False)
               for n,f in [('ById',[[0,False]]), ('ByCode',[[1,False]]), ('ByPair',[[1,True],[7,False]])]]
    insert = lambda id: dict(kind='insert',row=row(name,id))
    changed = row(name,4); changed[1] = None; changed[3] = ''; changed[6] = ''
    edited = [dict(kind='field',id=1,column=1,value=b'changed '.hex()), dict(kind='field',id=2,column=1,value=''),
              dict(kind='field',id=3,column=3,value=wide.payload(4096,3,3,True)), dict(kind='replace',id=4,row=changed),
              dict(kind='field',id=7,column=0,value=700), dict(kind='delete',id=5)] + [insert(id) for id in range(1000,1008)]
    stages = [dict(name='original',operations=[]), dict(name='edited',operations=edited),
              dict(name='regrown',operations=[dict(kind='delete',id=id) for id in range(20,40)] + [insert(id) for id in range(2000,2020)])]
    native = [insert(9000),dict(kind='field',id=9000,column=0,value=9001),dict(kind='delete',id=1000)]
    samples = [row(name,id) for id in [*range(48), *range(1000,1008), *range(2000,2020),9000,9001,1234567,1234568,9999]]
    extra = row(name,1); extra[1] = b'changed '.hex(); samples += [extra]
    for index in indexes:
        queries = [[r[c] for c,_ in index['fields']] for r in samples]
        index['queries'] = list({json.dumps(q):q for q in queries if all(v is not None for v in q)}.values())
    refusals = []
    if name == 'mixed-later':
        bad = row(name,9999); bad[4] = ''
        replacement = row(name,4); replacement[5] = ''
        refusals = [dict(name='disabled-text-insert',number=3315,operation=dict(kind='insert',row=bad)),
                    dict(name='disabled-memo-row',number=3315,operation=dict(kind='replace',id=4,row=replacement))]
    if name == 'unique-text':
        bad = row(name,9999); bad[1] = ''
        refusals = [dict(name='unique-empty',number=3022,operation=dict(kind='insert',row=bad))]
    return dict(name=name,fields=fields,allow_fields=allow,fixed_fields=['Fixed','Group'], indexes=indexes,
                table_order=['Notes','Items'] if name=='mixed-later' else ['Items','Notes'],
                creation_rows=[row(name,id) for id in range(48)], initial_rows=[normalize(row(name,id)) for id in range(48)],
                stages=stages,native=native,refusals=refusals)


def properties(data, case):
    catalog = wide.structure.catalog
    definition, pages, _ = catalog._discover_catalog(data)
    name, prop = (catalog._ordinal(definition,n) for n in ('Name','LvProp'))
    group = next(g for g in definition['long_value_maps'] if g['column']==prop)
    owned = catalog._locator_pages(data,group['owned'],'LvProp owned')
    found = []
    for page in pages:
        image = catalog._page(data,page,'catalog')
        for entry in catalog._row_directory(image,page):
            if entry['hidden']: continue
            fields, _ = wide.structure.layout(image[entry['start']:entry['end']],definition['columns'])
            if fields[name] != b'Items': continue
            reached = set(); payload = wide.structure.payload(data,fields[prop],owned,reached)
            expected = bytearray.fromhex('4b4b4400210000008000080052657175697265640f00416c6c6f775a65726f4c656e677468')
            for n,kind,_ in case['fields']:
                encoded = n.encode('cp1252'); records = bytearray()
                if kind in (10,12): records += bytes.fromhex('0900010101000100') + bytes([255 if n in case['allow_fields'] else 0])
                records += bytes.fromhex('090001010000010000')
                expected += (12+len(encoded)+len(records)).to_bytes(4,'little') + b'\x01\x00' + (6+len(encoded)).to_bytes(4,'little') + len(encoded).to_bytes(2,'little') + encoded + records
            require(payload == expected, 'Complete EXP-0266 named column property bytes')
            found.append(dict(header=fields[prop].hex(),length=len(payload),sha256=hashlib.sha256(payload).hexdigest(),
                              fragments=sorted(reached), pages={str(p):hashlib.sha256(catalog._page(data,p,'property')).hexdigest() for p,_ in reached}))
    require(len(found)==1,'Exactly one Items property payload')
    require(len(found[0]['fragments']) == (2 if case['name']=='property-chain' else 1),'Expected property chain shape')
    return found[0]


def raw_check(data,case,expected,counters,receipt=None,previous=None):
    result = wide.raw_check(data,case,expected,counters,receipt,previous)
    result['properties'] = properties(data,case)
    return result


def normalized(capture,case,rows):
    result = wide.original_normalized(capture,case,rows)
    for table in result['user_tables']:
        for field in table['fields']:
            fixed = field['type']==4 or (table['name']=='Items' and field['name'] in case['fixed_fields'])
            allow = table['name']=='Items' and field['name'] in case['allow_fields']
            require(field == dict(name=field['name'],type=field['type'],size=field['size'],attributes=1 if fixed else 2,
                                  required=False,allow_zero_length=allow,default_value=''), 'Complete per-column DAO field properties')
    return result


def refusal_check(directory, notes):
    receipts = json.loads((directory/'refusals.json').read_text())
    errors = {'disabled-text-insert':'Encoding(ZeroLengthNotAllowed { ordinal: 4, physical_type: Text })',
              'disabled-memo-row':'Encoding(ZeroLengthNotAllowed { ordinal: 5, physical_type: Memo })',
              'resource':'Open(Header(Read(ResourceLimitExceeded { kind: TotalWorkUnits, requested: 1, maximum: 0 })))',
              'unique-empty':'Unsupported("duplicate unique key")'}
    require([r['name'] for r in receipts]==list(errors),'Complete refusal inventory')
    for r in receipts:
        source = (directory/f"{r['case']}-original.mdb").read_bytes()
        require((directory/f"refusal-{r['name']}-before.mdb").read_bytes() ==
                (directory/f"refusal-{r['name']}-after.mdb").read_bytes() == source and r['preserved'], 'Whole-image refusal preservation')
        require(r['error']==errors[r['name']], 'Declared refusal reason')
    return receipts


def inputs():
    paths = [Path(__file__),SCRIPT,ROOT/'crates/jet3/examples/empty_value_candidate.rs']
    return fixed.inputs() | {str(p.relative_to(ROOT)):identity(p) for p in paths}


def evaluate(directory, outbox):
    engine.aggregate(outbox)
    semantic = engine.evaluate(directory,outbox)
    manifest = json.loads((directory/MANIFEST).read_text())
    result = json.loads((outbox/'result.json').read_text())
    report = dict(document_type='dao_empty_value_lifecycle_report', status='failed',
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
                    require(refused['error'] is not None and refused['numbers']==[request['number']], 'Declared DAO refusal number')
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
    path = outbox/'empty-value-lifecycle-report.json'; suffix = 1
    while path.exists() and json.loads(path.read_text())!=report:
        suffix += 1; path = outbox/f'empty-value-lifecycle-report-{suffix}.json'
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
        engine.prepare(args.candidates,revision); fixed.stage_helpers(args.candidates)
    else:
        engine.prepare_continue(args.candidates,args.outbox,args.output,args.generator,revision)
        fixed.stage_helpers(args.output)


engine.CASE_NAMES = CASES
engine.SCRIPT = SCRIPT
engine.MANIFEST = MANIFEST
engine.recipe = recipe
engine.initial_row = row
engine.inputs = inputs
engine.refusal_check = refusal_check
engine.raw_check = raw_check
engine.normalized = normalized
engine.apply = apply
if __name__ == '__main__': raise SystemExit(main())
