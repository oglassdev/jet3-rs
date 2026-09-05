#!/usr/bin/env python3
"""Separate finite hosted creation-index expansion; original write runtime is frozen."""
import argparse
import copy
from decimal import Decimal
import json
import importlib.util
from pathlib import Path
import platform
import subprocess
from hosted_write_reanalysis import validate_environment
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('_creation_index_write',ROOT/'oracle/windows-dao/scripts/dao_write_diff.py')
original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
INVENTORY=ROOT/'oracle/windows-dao/protocol/v1_2/creation-index-scenarios.json'
PLAN=ROOT/'oracle/windows-dao/acquisition/creation-index-v1_2.plan.json'
load=original.load_json;digest=original.digest;fail=original.ValidationError
read_inventory=original.inventory


def expanded(case):
    case=copy.deepcopy(case);recipe=case['recipe']
    column=lambda name,kind:dict(name=name,kind=kind)
    index=lambda name,kind,fields:dict(name=name,kind=kind,fields=[dict(column=c,descending=d) for c,d in fields])
    if recipe=='long-depth-three':
        columns=[column('Id','Long')];rows=[[n] for n in range(-13900,13901)];indexes=[index('ById','primary',[(0,False)])]
    elif recipe=='nullable-numeric':
        columns=[column('A','Currency'),column('B','Double'),column('Tag','Long')];rows=[[n-60,float(n),n+1] for n in range(120)]
        for n in range(2):rows.extend([[None,None,121+n*3],[None,1.0,122+n*3],[1,None,123+n*3]])
        indexes=[index('ByKey','unique',[(0,False),(1,True)])]
    elif recipe=='multiple-long':
        columns=[column(n,'Long') for n in ['Id','Group','Value']];rows=[[n+1,n%3-1,n-100] for n in range(201)]
        indexes=[index('ZPrimary','primary',[(0,False)]),index('AGroup','ordinary',[(1,True)]),index('MMixed','unique',[(1,True),(2,False)])]
    else:raise fail('Unknown creation recipe')
    case['tables']=[dict(name='Rows',columns=columns,rows=rows,indexes=indexes,repeat=None)];case['relationship']=None;return case


def inventory():
    value=read_inventory(INVENTORY);value['scenarios']=[expanded(s) for s in value['scenarios']];return value


def query_values(row,index):return tuple(row[f['name']]['value'] for f in index['fields'])
def directed(row,index):
    result=[]
    for field in index['fields']:
        value=row[field['name']];sign=-1 if field['descending'] else 1
        result.append((0,0) if value['kind']=='null' else (sign,sign*(Decimal(value['value']) if value['kind']=='currency' else value['value'])))
    return tuple(result)

def queries(rows,index,case):
    if case['recipe']=='long-depth-three':return {(n,) for n in [-13900,-13899,-13701,-13700,-13699,-1,0,13899,13900]}
    return {query_values(row,index) for row in rows if all(row[f['name']]['kind']!='null' for f in index['fields'])}

def assert_indexes(observations,snapshot):
    case=next(s for s in inventory()['scenarios'] if s['id']==snapshot['scenario_id'])
    expected={(t['name'],i['name']):(t,i) for t in snapshot['tables'] for i in t['indexes']}
    keys=[(o['table'],o['index']) for o in observations]
    if len(keys)!=len(set(keys)) or set(keys)!=set(expected):raise fail('Index observation inventory')
    for obs in observations:
        table,index=expected[obs['table'],obs['index']];rows=[r['values'] for r in table['rows']];actual=obs['rows'];canonical=original.common.canonical_bytes
        if sorted(map(canonical,rows))!=sorted(map(canonical,actual)) or [directed(r,index) for r in actual]!=sorted(directed(r,index) for r in rows):raise fail('Complete directed traversal')
        observed=[tuple(s['query']) for s in obs['seeks']]
        if len(observed)!=len(set(observed)) or set(observed)!=queries(rows,index,case):raise fail('Declared finite Seek inventory')
        valid={}
        for row in rows:valid.setdefault(query_values(row,index),set()).add(canonical(row))
        for seek in obs['seeks']:
            if canonical(seek['row']) not in valid[tuple(seek['query'])]:raise fail('Full-key Seek row')

original.INVENTORY=INVENTORY;original.PLAN=PLAN;original.inventory=inventory;original.assert_indexes=assert_indexes

def run(root,label,command):
    done=subprocess.run(command,capture_output=True,text=True,timeout=300);(root/(label+'.stdout.log')).write_text(done.stdout);(root/(label+'.stderr.log')).write_text(done.stderr)
    if done.returncode:raise fail(label+' failed')

def snapshot(root,reader,scenario,revision,label):
    run(root,label,[str(reader),'snapshot',str(root/'database.mdb'),'--scenario',scenario['id'],'--inventory','creation-index','--out',str(root/'rust'),'--source-revision',revision])
    value=load(root/'rust/snapshot.json');original.bind_snapshot(value,scenario['id'],revision,'rust',digest(root/'database.mdb'));original.assert_recipe(value,scenario);original.validate_write_coverage(load(root/'rust/coverage.json'),inventory()['scenarios'],value)

def prepare(out,generator,reader,revision):
    out.mkdir(parents=True,exist_ok=False);manifest=dict(document_type='dao_write_preparation',protocol_version='1.2.0',source_revision=revision,producer_os=platform.system(),inventory_sha256=digest(INVENTORY),scenarios=[])
    try:
        for scenario in inventory()['scenarios']:
            root=out/scenario['id'];root.mkdir();entry=dict(scenario_id=scenario['id'],status='failed',error=None);manifest['scenarios'].append(entry)
            try:
                run(root,'generate',[str(generator),'generate',scenario['id'],str(root)])
                entry.update(database_sha256=digest(root/'database.mdb'),layout_sha256=digest(root/'layout.json'))
                snapshot(root,reader,scenario,revision,'snapshot')
                if digest(root/'database.mdb')!=entry['database_sha256']:raise fail('Unix read changed candidate')
                entry['status']='prepared'
            except Exception as error:entry['error']=str(error);raise
    finally:original.common.write_canonical(out/'preparation.json',manifest)

def resnapshot(out,generator,reader,revision):
    prepared=load(out/'preparation.json');cases=inventory()['scenarios']
    if prepared['source_revision']!=revision or prepared['inventory_sha256']!=digest(INVENTORY) or [s['scenario_id'] for s in prepared['scenarios']]!=[s['id'] for s in cases]:raise fail('Downloaded preparation binding')
    for scenario,entry in zip(cases,prepared['scenarios']):
        root=out/scenario['id']
        if entry['status']!='prepared' or digest(root/'database.mdb')!=entry['database_sha256'] or digest(root/'layout.json')!=entry['layout_sha256']:raise fail('Downloaded artifact identity')
        run(root,'windows-layout',[str(generator),'verify',scenario['id'],str(root)]);snapshot(root,reader,scenario,revision,'windows-snapshot')
        if digest(root/'database.mdb')!=entry['database_sha256']:raise fail('Windows read changed candidate')
    original.common.write_canonical(out/'reader.json',dict(source_revision=revision,reader_os=platform.system()))

def evaluate(out):
    try:
        environment=load(out/'environment.json');validate_environment(environment);manifest=load(out/'dao-manifest.raw.json')
        if manifest['environment_sha256']!=digest(out/'environment.json'):raise fail('Provider receipt identity')
        prepared=load(out/'preparation.json')
        for scenario,entry in zip(inventory()['scenarios'],prepared['scenarios']):
            root=out/scenario['id'];layout=load(root/'layout.json')
            if digest(root/'layout.json')!=entry['layout_sha256'] or layout['scenario_id']!=scenario['id']:raise fail('Layout receipt identity')
            expected={i['name']:(i['depth'],i['entries']) for i in scenario['trees']}
            if len(layout['trees'])!=len(expected) or {i['name']:(i['depth'],i['entries']) for i in layout['trees']}!=expected:raise fail('Tree boundary receipt')
        original.evaluate_checked(out);report=load(out/'report.json');report['document_type']='dao_creation_index_report';report['seek_scope']='nine declared boundaries for deep Long; every non-null full key for other recipes';original.common.write_canonical(out/'report.json',report)
    except Exception as error:
        original.common.write_canonical(out/'report.json',dict(document_type='dao_creation_index_report',outcome='no_outcome',error=str(error),support_matrix_movement=False));raise

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True);p=sub.add_parser('plan');p.add_argument('sha256');sub.add_parser('inventory')
    for command in ['prepare','snapshot']:
        p=sub.add_parser(command);p.add_argument('out',type=Path);p.add_argument('generator',type=Path);p.add_argument('reader',type=Path);p.add_argument('revision')
    p=sub.add_parser('evaluate');p.add_argument('out',type=Path);args=parser.parse_args()
    if args.command=='plan':original.validate_plan(args.sha256)
    elif args.command=='inventory':inventory()
    elif args.command=='prepare':prepare(args.out,args.generator,args.reader,args.revision)
    elif args.command=='snapshot':resnapshot(args.out,args.generator,args.reader,args.revision)
    else:evaluate(args.out)
if __name__=='__main__':main()
