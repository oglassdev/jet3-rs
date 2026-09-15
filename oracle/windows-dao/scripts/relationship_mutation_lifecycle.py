#!/usr/bin/env python3
"""Paired non-cascading relationship lifecycles and native continuations."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import hashlib
import relationship_mutation_structure as raw

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_suffix('.ps1')
MANIFEST = 'relationship-mutation-lifecycle.json'


def identity(path):
    data = path.read_bytes()
    return dict(size=len(data),sha256=hashlib.sha256(data).hexdigest())


def write(path, value):
    path.write_text(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n')


def insert(table,row): return dict(kind='insert',table=table,row=row)
def field(table,id,column,value): return dict(kind='field',table=table,id=id,column=column,value=value)
def replace(table,id,row): return dict(kind='replace',table=table,id=id,row=row)
def delete(table,id): return dict(kind='delete',table=table,id=id)
def child(id,parent,body=None,blob=None):
    return [id,parent,(f'i{id}'.encode() if body is None else body).hex(),(raw.pattern(33,id) if blob is None else blob).hex()]


def recipe():
    stages = [dict(name='original',operations=[]),
      dict(name='inserted',operations=[insert('Parent',[20,b'twenty'.hex()]),insert('Child',child(104,3)),insert('Child',child(105,3)),insert('Child',child(106,None))]),
      dict(name='equal-field',operations=[field('Child',104,1,3)]),
      dict(name='equal-row',operations=[replace('Child',105,child(105,3,b'changed'*5,raw.pattern(4096,105)))]),
      dict(name='other-key',operations=[field('Child',104,0,114)]),
      dict(name='keys-and-nulls',operations=[field('Child',102,1,3),replace('Child',103,[103,2,b'm103'.hex(),b'D'.hex()]),replace('Child',114,child(114,None,b'i104',raw.pattern(33,104)))]),
      dict(name='payloads-and-parents',operations=[replace('Child',100,[100,1,(b'z'*4096).hex(),raw.pattern(33,700).hex()]),field('Parent',10,0,11),delete('Parent',20)]),
      dict(name='ordered-deletes',operations=[delete('Child',100),delete('Child',101),delete('Parent',1)]),
      dict(name='branch-growth',operations=[insert('Child',child(id,None if id%5==0 else 2 if id%2==0 else 3,b'x',b'y')) for id in range(1000,1280)]),
      dict(name='branch-edits',operations=[replace('Child',id,child(id,3 if id%2==0 else 2,b'k'*33,raw.pattern(33,id))) for id in range(1020,1040)] + [delete('Child',id) for id in range(1100,1140)])]
    refusals = [dict(name='orphan-insert',number=3201,operation=insert('Child',child(999,999))),
      dict(name='orphan-field',number=3201,operation=field('Child',102,1,999)),
      dict(name='orphan-row',number=3201,operation=replace('Child',103,[103,999,b'm103'.hex(),b'D'.hex()])),
      dict(name='parent-field',number=3200,operation=field('Parent',1,0,101)),
      dict(name='parent-row',number=3200,operation=replace('Parent',1,[101,b'one'.hex()])),
      dict(name='parent-delete',number=3200,operation=delete('Parent',1)),
      dict(name='resource',number=None,operation=dict(field('Child',102,1,3),limited=True))]
    native=[insert('Parent',[30,b'thirty'.hex()]),insert('Child',child(130,30)),field('Child',130,1,3),field('Parent',30,0,31),delete('Child',102),delete('Parent',31)]
    continuation=[insert('Parent',[40,b'forty'.hex()]),insert('Child',child(140,40)),replace('Child',140,child(140,3,b'q'*4096,b'r')),delete('Parent',40)]
    return dict(stages=stages,refusals=refusals,native=native,continuation=continuation)


def initial():
    return {'Parent':{i:[i,label.encode().hex()] for i,label in [(1,'one'),(2,'two'),(3,'three'),(10,'ten')]},
            'Child':{100:[100,1,b'm100'.hex(),raw.pattern(4096,100).hex()],101:[101,1,(b'B'*4096).hex(),b'B'.hex()],102:[102,2,None,None],103:[103,None,b'm103'.hex(),b'D'.hex()]}}


def apply(model,counters,operation):
    name=operation['table'];rows=model[name];kind=operation['kind'];before=copy.deepcopy(rows)
    if kind=='delete': del rows[operation['id']]
    elif kind=='insert': rows[operation['row'][0]]=operation['row'].copy()
    else:
        row=rows.pop(operation['id'])
        if kind=='field': row[operation['column']]=operation['value']
        else: row=operation['row'].copy()
        rows[row[0]]=row
    if kind=='insert':
        for ordinal,column in enumerate([0] if name=='Parent' else [0,1]):
            if operation['row'][column] not in {r[column] for r in before.values()}: counters[name][ordinal][1]+=1
    elif name=='Child' and (kind in ('replace','delete') or (kind=='field' and operation['column']==1)):
        state=counters[name][1]
        if state[0]>0:
            state[0]-=1;state[1]=min(state[1],state[0])
    raw.req(all(r[1] is None or r[1] in model['Parent'] for r in model['Child'].values()),'recipe referential integrity')


def expected(model):
    return ({id:bytes.fromhex(row[1]).decode('cp1252') for id,row in model['Parent'].items()},
            {id:[row[1],None if row[2] is None else bytes.fromhex(row[2]),None if row[3] is None else bytes.fromhex(row[3])] for id,row in model['Child'].items()})


def prepare(source, output, generator, revision):
    output.mkdir()
    plan=recipe();cases=[]
    for replica in (1,2):
        seed=source/f'r{replica}-created.mdb';name=f'r{replica}';staged=output/f'{name}-source.mdb';shutil.copyfile(seed,staged)
        recipe_path=output/f'{name}-recipe.json';write(recipe_path,plan)
        destination=output/f'{name}-candidate'
        subprocess.run([str(generator),str(staged),str(recipe_path),str(destination)],check=True)
        stages=[]
        for stage in plan['stages']:
            filename=f"{name}-{stage['name']}.mdb";shutil.copyfile(destination/f"{stage['name']}.mdb",output/filename)
            stages.append(dict(stage,file=filename,identity=identity(output/filename)))
        cases.append(dict(name=name,source=staged.name,source_identity=identity(staged),stages=stages))
    inputs={str(p.relative_to(ROOT)):identity(p) for p in [Path(__file__),SCRIPT,Path(__file__).with_name('relationship_mutation_dao.ps1'),Path(__file__).with_name('relationship_mutation_structure.py'),ROOT/'crates/jet3/examples/relationship_mutation_candidate.rs']}
    write(output/MANIFEST,dict(source_revision=revision,round='mutations',cases=cases,recipe=plan,inputs=inputs))


def capture(outbox,record,model,counters,baseline,receipt=None):
    path=outbox/record['file'];raw.req(identity(path)==record['after'],'retained capture identity')
    observed=raw.observe(path,record,expected(model))
    for name in ('Parent','Child'):
        raw.req([[i['first_word'],i['counter']] for i in observed['indexes'][name]]==counters[name],f'{record["file"]} {name} counters')
        if receipt is not None:
            raw.req([r['values'] for r in receipt[name]['rows']]==list(model[name].values()) or
                    sorted([r['values'] for r in receipt[name]['rows']],key=lambda r:r[0])==sorted(model[name].values(),key=lambda r:r[0]),'complete Rust receipt rows')
            raw.req(len(receipt[name]['indexes'])==len(observed['indexes'][name]),'Rust/raw physical index inventory')
            for actual,index in zip(receipt[name]['indexes'],observed['indexes'][name]):
                entries=[key+int(page).to_bytes(3,'big').hex()+bytes([slot]).hex() for key,page,slot in actual['entries']]
                raw.req(actual['counter']==index['counter'] and actual['first_word']==index['first_word'] and entries==index['entries_hex'],'Rust/raw index receipt')
            raw.req([r['locator'] for r in receipt[name]['rows']]==[[r['locator']['page'],r['locator']['row']] for r in observed['rows'][name]],'Rust/raw logical locators')
    if baseline is not None:
        for key in ('notes_page_hashes','raw_schema','relationship_records','relationship_catalog_row','relationship_system_indexes','dao_schema'):
            raw.req(observed[key]==baseline[key],f'{record["file"]} preserved {key}')
    return observed


def advance_locators(tracked,operation):
    for tables in tracked.values():
        if tables is None: continue
        rows=tables[operation['table']];kind=operation['kind']
        if kind=='insert': rows[operation['row'][0]]=None
        elif kind=='delete': del rows[operation['id']]
        elif kind=='replace': rows[operation['row'][0]]=rows.pop(operation['id'])
        elif operation['column']==0: rows[operation['value']]=rows.pop(operation['id'])


def check_locators(tracked,role,observation):
    actual={name:{row['id']:row['locator'] for row in observation['rows'][name]} for name in ('Parent','Child')}
    previous=tracked.get(role)
    if previous is not None:
        for name,rows in actual.items():
            raw.req(rows.keys()==previous[name].keys(),'logical locator row inventory')
            for id,locator in rows.items():
                raw.req(previous[name][id] is None or previous[name][id]==locator,'surviving logical locator preserved')
    tracked[role]=actual


def semantic(observed):
    return dict(schema=observed['dao_schema'],rows={name:[(r['id'],r['values']) for r in rows] for name,rows in observed['rows'].items()},
                counters={name:[[i['first_word'],i['counter']] for i in indexes] for name,indexes in observed['indexes'].items()},relations=observed['relationship_catalog_row'])


def evaluate(directory,outbox):
    manifest=json.loads((directory/MANIFEST).read_text());result=json.loads((outbox/'result.json').read_text())
    report=dict(status='failed',source_revision=manifest['source_revision'],round=manifest['round'],captures=[],refusals=[],error=None,environment=result.get('environment'),
                result_identity=identity(outbox/'result.json'),manifest_identity=identity(directory/MANIFEST),
                verifier_inputs={p.name:identity(p) for p in [Path(__file__),Path(raw.__file__),Path(raw.catalog.__file__),Path(raw.indexes.__file__),Path(raw.allocation.__file__),Path(raw.relationships.__file__)]})
    try:
        raw.req(result['error'] is None,'native producer completed')
        raw.req(result['source_revision']==manifest['source_revision'],'native source revision')
        raw.req(result['environment']['provider']=='DAO.DBEngine.36' and result['environment']['bits']==32 and result['environment']['dll_sha256']=='4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac','recorded DAO provider')
        raw.req(result['manifest']==identity(directory/MANIFEST),'manifest identity')
        raw.req([c['name'] for c in result['cases']]==[c['name'] for c in manifest['cases']],'case inventory')
        for case,observed_case in zip(manifest['cases'],result['cases']):
            model=copy.deepcopy(case.get('initial',initial()));model={name:{int(id):row for id,row in rows.items()} for name,rows in model.items()}
            counters=copy.deepcopy(case.get('counters',{'Parent':[[0,4]],'Child':[[0,4],[4,3]]}));baseline={}
            start=case.get('locators');start=None if start is None else {n:{int(id):loc for id,loc in rs.items()} for n,rs in start.items()}
            tracked={role:copy.deepcopy(start) for role in ('candidate','control')}
            requests=[op for stage in case['stages'] for op in stage['operations']]+manifest['recipe']['native']*2
            raw.req([op['request'] for op in observed_case['operations']]==requests and all(op['status']=='success' for op in observed_case['operations']),'exact native operation inventory')
            for stage,pair in zip(case['stages'],observed_case['stages']):
                raw.req(stage['name']==pair['name'],'stage order')
                for operation in stage['operations']:
                    apply(model,counters,operation);advance_locators(tracked,operation)
                current={}
                raw.req(pair['candidate']['before']==stage['identity'],'candidate capture starts from declared input')
                for role in ('candidate','control'):
                    receipt=json.loads((directory/f"{case['name']}-candidate"/f"{stage['name']}.snapshot.json").read_text()) if role=='candidate' else None
                    current[role]=capture(outbox,pair[role],model,counters,baseline.get(role),receipt)
                    baseline.setdefault(role,current[role]);check_locators(tracked,role,current[role])
                    if role=='candidate': raw.req(current[role]['page0_sha256']==baseline[role]['page0_sha256'],'Rust preserves the complete header page')
                    report['captures'].append(dict(case=case['name'],stage=stage['name'],role=role,observation=current[role]))
                raw.req(semantic(current['candidate'])==semantic(current['control']),'paired complete semantics')
            raw.req(len(case['stages'])==len(observed_case['stages']),'complete stage inventory')
            for operation in manifest['recipe']['native']:
                apply(model,counters,operation);advance_locators(tracked,operation)
            successor={role:capture(outbox,observed_case['native'][role],model,counters,baseline[role]) for role in ('candidate','control')}
            raw.req(semantic(successor['candidate'])==semantic(successor['control']),'native successor pair')
            for role in ('candidate','control'):
                check_locators(tracked,role,successor[role])
                report['captures'].append(dict(case=case['name'],stage='native',role=role,observation=successor[role]))
            observed_case['expected_after']=model;observed_case['counters_after']=counters;observed_case['locators_after']=tracked
            if manifest['round']=='mutations':
                receipts=json.loads((directory/f"{case['name']}-candidate"/'refusals.json').read_text())
                raw.req([r['name'] for r in receipts]==[r['name'] for r in manifest['recipe']['refusals']],'Rust refusal inventory')
                for request,receipt in zip(manifest['recipe']['refusals'],receipts):
                    raw.req(receipt['preserved'] and (directory/f"{case['name']}-candidate"/f"refusal-{request['name']}.mdb").read_bytes()==(directory/case['source']).read_bytes(),'Rust refusal whole image')
                    raw.req(('RelationshipConstraint' in receipt['error']) if request['number'] else ('TotalWorkUnits' in receipt['error']),'Rust refusal category')
                raw.req([r['name'] for r in observed_case['refusals']]==[r['name'] for r in manifest['recipe']['refusals'] if r['number']],'native refusal inventory')
                for request,attempt in zip([r for r in manifest['recipe']['refusals'] if r['number']],observed_case['refusals']):
                    pair={}
                    for role in ('candidate','control'):
                        refused=attempt[role];raw.req(refused['operation']['status']=='rejected' and refused['operation']['error']['numbers']==[request['number']],'DAO refusal number')
                        raw.req(refused['operation']['request']==request['operation'] and refused['operation']['before']==baseline[role]['identity'],'native refusal input and operation')
                        raw.req(identity(outbox/refused['capture']['file'])==refused['capture']['after']==refused['operation']['after'],'native refusal closed identity')
                        after=raw.observe(outbox/refused['capture']['file'],refused['capture'],expected(initial()))
                        pair[role]=dict(semantic(after),page0_counter=after['page0_counter'],page0_sha256=after['page0_sha256'])
                        for key in ('raw_schema','relationship_records','relationship_catalog_row','relationship_system_indexes','dao_schema'):
                            raw.req(after[key]==baseline[role][key],'native refusal metadata')
                        raw.req(after['rows']==baseline[role]['rows'],'DAO refusal complete rows preserved')
                        raw.req([[i['entries_hex'] for i in after['indexes'][n]] for n in ('Parent','Child')]==[[i['entries_hex'] for i in baseline[role]['indexes'][n]] for n in ('Parent','Child')],'DAO refusal keys preserved')
                        raw.req(after['notes_page_hashes']==baseline[role]['notes_page_hashes'],'DAO refusal Notes')
                        raw.req(after['maps']==baseline[role]['maps'] and after['free_pages']==baseline[role]['free_pages'],'DAO refusal allocation maps')
                        report['refusals'].append(dict(case=case['name'],name=request['name'],role=role,counters={n:[[i['first_word'],i['counter']] for i in after['indexes'][n]] for n in ('Parent','Child')},identity=after['identity'],page0_counter=after['page0_counter'],page0_before=baseline[role]['page0_counter']))
                    raw.req(pair['candidate']==pair['control'],'paired refusal semantics and counters')
            if manifest['round']=='mutations':
                for phase in ('branch-growth','branch-edits'):
                    for role in ('candidate','control'):
                        branch=next(c for c in report['captures'] if c['case']==case['name'] and c['stage']==phase and c['role']==role)
                        indexes=branch['observation']['indexes']['Child']
                        raw.req(len(indexes[1]['nodes'])>1,'both roles retain a foreign branch index')
                        if role=='candidate': raw.req(len(indexes[0]['nodes'])>1,'Rust primary branch index exercised')
        report['status']='accepted'
    except Exception as error: report['error']=str(error)
    target=outbox/'relationship-mutation-report.json';n=1
    while target.exists() and json.loads(target.read_text())!=report:
        n+=1;target=outbox/f'relationship-mutation-report-{n}.json'
    write(target,report)
    if report['status']=='accepted': write(outbox/'continuation-state.json',result)
    print(report['status'],report['error'],target)
    return report


def prepare_continue(directory,outbox,output,generator,revision):
    manifest=json.loads((directory/MANIFEST).read_text());state=json.loads((outbox/'continuation-state.json').read_text())
    output.mkdir();cases=[]
    plan=dict(stages=[dict(name='original',operations=[]),dict(name='continued',operations=manifest['recipe']['continuation'])],
              refusals=[],native=[field('Child',140,1,2),delete('Child',130)],continuation=[])
    for prior in state['cases']:
        for role in ('candidate','control'):
            name=f"{prior['name']}-{role}";source=outbox/prior['native'][role]['file'];seed=output/f'{name}-source.mdb';shutil.copyfile(source,seed)
            recipe_path=output/f'{name}-recipe.json';write(recipe_path,plan)
            destination=output/f'{name}-candidate';subprocess.run([str(generator),str(seed),str(recipe_path),str(destination)],check=True)
            stages=[]
            for stage in plan['stages']:
                filename=f"{name}-{stage['name']}.mdb";shutil.copyfile(destination/f"{stage['name']}.mdb",output/filename)
                stages.append(dict(stage,file=filename,identity=identity(output/filename)))
            cases.append(dict(name=name,source=seed.name,source_identity=identity(seed),stages=stages,initial=prior['expected_after'],counters=prior['counters_after'],locators=prior['locators_after'][role]))
    write(output/MANIFEST,dict(source_revision=revision,round='continuation',cases=cases,recipe=plan,previous_manifest=identity(directory/MANIFEST),previous_state=identity(outbox/'continuation-state.json')))


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('source',type=Path);p.add_argument('output',type=Path);p.add_argument('generator',type=Path);p.add_argument('revision')
    p=sub.add_parser('evaluate');p.add_argument('directory',type=Path);p.add_argument('outbox',type=Path)
    p=sub.add_parser('prepare-continue');p.add_argument('directory',type=Path);p.add_argument('outbox',type=Path);p.add_argument('output',type=Path);p.add_argument('generator',type=Path);p.add_argument('revision')
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.source,args.output,args.generator,args.revision)
    elif args.command=='prepare-continue': prepare_continue(args.directory,args.outbox,args.output,args.generator,args.revision)
    else: return int(evaluate(args.directory,args.outbox)['status']!='accepted')

if __name__=='__main__': raise SystemExit(main())
