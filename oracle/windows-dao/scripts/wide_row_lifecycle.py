#!/usr/bin/env python3
"""Bounded wide-row lifecycle, native successors and Rust edits of native controls."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

import index_capacity as base
import wide_row_lifecycle_structure as structure
from index_tree_mutation import identity, notes_identity, require

ROOT=Path(__file__).resolve().parents[3]
SCRIPT=Path(__file__).with_suffix('.ps1')
MANIFEST='wide-row-lifecycle.json'
CONFIG={'vars2':(2,5,False),'vars3':(3,5,False),'vars8':(8,5,False),'vars32':(32,5,False),
        'vars254':(254,5,False),'fixed260':(2,260,False),'fixed767':(2,767,False),'mixed':(4,5,True)}
engine=base.engine
original_inputs=base.inputs
original_normalized=engine.normalized


def payload(length,id,column,text):
    alphabet=b'aAezZ\xe9\xc9\xc6\xe6\xdf\x8a\x9a'
    return bytes(alphabet[(n*7+id*13+column*31)%len(alphabet)] if text else
                 (n*37+id*13+column*31)%256 for n in range(length)).hex()


def maximum(name):
    return {2:510,3:765,8:1988,32:1600,254:1400}.get(CONFIG[name][0],510)


def row(name,id,total=None,sparse=False):
    variables,fixed,mixed=CONFIG[name];count=(fixed-5+254)//255;values=[id]
    for i in range(count):values.append(payload(min(fixed-5-255*i,255),id,i+1,True))
    actual=2 if mixed else variables;total=maximum(name) if total is None else total
    for i in range(actual):
        length=total//actual+int(i<total%actual)
        values.append(None if sparse and i+1!=actual else payload(1 if sparse else length,id,i+count+1,mixed or i%2==0))
    if mixed:values.extend([payload(80,id,30,True),payload(80,id,31,False)])
    return values


def recipe(name):
    variables,fixed,mixed=CONFIG[name];count=(fixed-5+254)//255
    fields=[['Id',4,4]]+[[f'F{i:03}',10,min(fixed-5-255*i,255)] for i in range(count)]
    fields += [[f'V{i:03}',12 if mixed and i==2 else 11 if mixed and i==3 else 10 if mixed or i%2==0 else 9,
                0 if mixed and i>=2 else 255] for i in range(variables)]
    indexes=[dict(name='ById',fields=[[0,False]],primary=True,unique=True,required=True,ignore=False)]
    if variables<=8:indexes.append(dict(name='ByText',fields=[[count+1,True]],primary=False,unique=False,required=False,ignore=False))
    replace=lambda id,value:dict(kind='replace',id=id,row=value)
    insert=lambda id,value=None:dict(kind='insert',row=row(name,id) if value is None else value)
    operations=[replace(0,row(name,0))]
    operations += [insert(id,row(name,id,max(variables,min(maximum(name),width)))) for id,width in zip(range(1000,1004),(240,249,495,751))]
    operations += [dict(kind='delete',id=1),dict(kind='field',id=7,column=0,value=99),dict(kind='delete',id=2)]
    for index in indexes:
        values=[row(name,id) for id in (0,8,9000,1234567,12345)]
        index['queries']=list({json.dumps([r[c] for c,_ in index['fields']]):[r[c] for c,_ in index['fields']] for r in values}.values())
    return dict(name=name,fields=fields,fixed_fields=[f'F{i:03}' for i in range(count)],indexes=indexes,
                initial_rows=[row(name,id) for id in range(16)],stages=[dict(name='original',operations=[]),
                dict(name='sparse',operations=[replace(0,row(name,0,1,True))]),dict(name='regrown',operations=operations)],
                native=[insert(9000),dict(kind='field',id=9000,column=0,value=9001),dict(kind='delete',id=1000)])


def raw_check(data,case,expected,counters,receipt=None,previous=None):
    result=base.raw_check(data,case,expected,counters,receipt,previous)
    table=base.tables(data)['Items'];pages,_=structure.catalog._table_pages(data,table)
    for column in table['columns']:
        require((column['storage']=='fixed')==(column['name']=='Id' or column['name'] in case['fixed_fields']), 'Exact fixed/variable schema storage')
    if receipt:
        require(receipt['classes']=={name:[c['class'] for c in definition['columns']] for name,definition in base.tables(data).items()}, 'Complete Rust storage class receipt')
    raw=structure.table_rows(data,table,pages)
    result['rows']=[dict(id=r['values'][0],**r['layout']) for r in sorted(raw,key=lambda r:r['values'][0])]
    result['maps']=structure.maps(data)
    return result


def normalized(capture,case,rows):
    result=original_normalized(capture,case,rows)
    for table in result['user_tables']:
        for field in table['fields']:
            fixed=field['type']==4 or (table['name']=='Items' and field['name'] in case['fixed_fields'])
            require(field==dict(name=field['name'],type=field['type'],size=field['size'],attributes=1 if fixed else 2,
                                required=False,allow_zero_length=False,default_value=''), 'Complete default DAO field properties')
    return result


def refusal_check(directory,notes):
    receipts=json.loads((directory/'refusals.json').read_text())
    require([r['name'] for r in receipts]==['jump-ordinal','end-low','variable-count'],'Corruption refusal inventory')
    source=(directory/'vars3-original.mdb').read_bytes()
    for receipt in receipts:
        before=(directory/f"refusal-{receipt['name']}-before.mdb").read_bytes()
        after=(directory/f"refusal-{receipt['name']}-after.mdb").read_bytes()
        expected=bytearray(source);expected[receipt['offset']]=receipt['value']
        require(before==after==expected and receipt['preserved'] and receipt['error'],'Exact malformed input and whole-image refusal')
        require(notes_identity(after)==notes,'Corruption refusal preserves Notes')
        try:structure.table_rows(before,base.tables(before)['Items'],structure.catalog._table_pages(before,base.tables(before)['Items'])[0])
        except (ValueError,structure.catalog.DecodeError):pass
        else:raise ValueError('Independent raw decoder admitted malformed row')
    return receipts


def inputs():
    files=[Path(__file__),SCRIPT,Path(structure.__file__),ROOT/'crates/jet3/examples/wide_row_candidate.rs',
           *sorted((ROOT/'crates/jet3/examples/wide_row_support').glob('*.rs')),
           Path(structure.map_record.__code__.co_filename)]
    return original_inputs()|{str(p.relative_to(ROOT)):identity(p) for p in files}


def prepare(candidates,revision):
    manifest=engine.prepare(candidates,revision)
    shutil.copy2(Path(engine.__file__).with_suffix('.ps1'),candidates/'numeric_index_mutation.ps1')
    shutil.copy2(Path(__file__).with_name('field_update.ps1'),candidates/'field_update.ps1')
    return manifest


def prepare_continue(candidates,outbox,output,generator,revision):
    engine.prepare_continue(candidates,outbox,output,generator,revision)
    shutil.copy2(Path(engine.__file__).with_suffix('.ps1'),output/'numeric_index_mutation.ps1')
    shutil.copy2(Path(__file__).with_name('field_update.ps1'),output/'field_update.ps1')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('candidates',type=Path);p.add_argument('revision')
    p=sub.add_parser('evaluate');p.add_argument('candidates',type=Path);p.add_argument('outbox',type=Path)
    p=sub.add_parser('prepare-continue');p.add_argument('candidates',type=Path);p.add_argument('outbox',type=Path);p.add_argument('output',type=Path);p.add_argument('generator',type=Path);p.add_argument('revision')
    args=parser.parse_args()
    if args.command=='evaluate':aggregate(args.outbox);return int(evaluate(args.candidates,args.outbox)['status']!='accepted')
    revision=subprocess.check_output(['git','rev-parse',args.revision],cwd=ROOT,text=True).strip()
    if args.command=='prepare':prepare(args.candidates,revision)
    else:prepare_continue(args.candidates,args.outbox,args.output,args.generator,revision)


base.raw_rows.table_rows=structure.table_rows
engine.CASE_NAMES=tuple(CONFIG);engine.SCRIPT=SCRIPT;engine.MANIFEST=MANIFEST
engine.recipe=recipe;engine.initial_row=lambda name,id:row(name,id)
engine.raw_check=raw_check;engine.refusal_check=refusal_check;engine.inputs=inputs;engine.normalized=normalized
aggregate=engine.aggregate;evaluate=engine.evaluate
if __name__=='__main__':raise SystemExit(main())
