#!/usr/bin/env python3
"""Thirteen-case hosted update adapter with finite Long index observations."""
import copy
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
def private(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'oracle/windows-dao/scripts'/file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
allocation=private('_indexed_allocation','dao_row_allocation_diff.py');base=allocation.base
keys=private('_indexed_keys','single_leaf_key.py')
base.INVENTORY=ROOT/'oracle/windows-dao/protocol/v1_2/indexed-update-scenarios.json'
base.PLAN=ROOT/'oracle/windows-dao/acquisition/indexed-update-v1_2.plan.json'
old_evaluate=base.evaluate_checked
IDS=allocation.IDS+['DAO-UPDATE-'+v for v in ['PRIMARY-KEY','DESCENDING-KEY','FULL-LEAF-KEY','INDEXED-PAYLOAD']]
require=allocation.require

def recipe(scenario,role):
    request=scenario['request']
    if request.get('kind')!='indexed_field':return allocation.recipe(scenario,role)
    result=copy.deepcopy(scenario);table,=[t for t in result['tables'] if t['name']==request['table']]
    target,=[r for r in table['rows'] if r[request['selector_column']]==request['selected']]
    if role=='after':target[request['column']]=request['replacement']
    else:require(role=='before','Unknown role')
    return result

def inventory():
    value=base.load_json(base.INVENTORY)
    require(value['document_type']=='dao_update_scenario_inventory' and value['protocol_version']=='1.2.0' and [s['id'] for s in value['scenarios']]==IDS,'Thirteen-case inventory')
    historical=base.load_json(ROOT/'oracle/windows-dao/protocol/v1_2/row-allocation-scenarios.json')['scenarios'];require(value['scenarios'][:9]==historical,'Historical cases changed')
    for scenario in value['scenarios']:
        require(scenario['operation']==dict(mode='dao_open_rust_update',expected_outcome='success',error_class=None) and not set(scenario['required_branches'])-base.write.protocol.load_branch_ids() and scenario['coverage'],'Operation/coverage');recipe(scenario,'after')
    require(value['deferred_requirements'],'Missing limitations');return value

def check_preservation(root,scenario,receipt):
    request=scenario['request']
    if request.get('kind')!='indexed_field':return allocation.check_preservation(root,scenario,receipt)
    require(receipt.get('scenario_id')==scenario['id'] and receipt.get('request')==request and receipt.get('preserved') is True,'Indexed request binding')
    before,after=[(root/role/'database.mdb').read_bytes() for role in base.ROLES]
    for role in base.ROLES:require(receipt[role+'_sha256']==base.write.digest(root/role/'database.mdb'),'Indexed image identity')
    c=keys.catalog;definition,_,objects=c._discover_catalog(before);name,ident=[c._ordinal(definition,n) for n in ('Name','Id')];roots=[o['values'][ident] for o in objects if o['values'][name]=='Items'];require(len(roots)==1 and roots[0]==receipt['root'],'Root binding')
    table=c._definition(before,roots[0]);pages,lval=c._table_pages(before,table);records=c._table_rows(before,table,pages);require(not lval,'Unexpected LVAL')
    declaration=scenario['tables'][0];require(sorted(r['values'] for r in records)==sorted(declaration['rows']),'Original requested rows')
    selected,=[r for r in records if r['values'][request['selector_column']]==request['selected']]
    directory=c._row_directory(c._page(before,selected['page'],'data'),selected['page']);offset=selected['page']*2048+directory[selected['row']]['start']+1+table['columns'][request['column']]['fixed_offset']
    index,=table['physical_indexes'];require(receipt['page']==selected['page'] and receipt['slot']==selected['row'] and receipt['column']==request['column'] and receipt['offset']==offset and receipt['index']==index['root'] and receipt['index_offset']==index['root']*2048+248 and receipt['index_length']==len(records)*9,'Independent indexed coordinates')
    if request['column']==0:
        arm=dict(rows=declaration['rows'],selected=request['selected'],replacement=request['replacement'],descending=declaration['indexes'][0]['fields'][0]['descending'],primary=declaration['indexes'][0]['kind']=='primary')
        keys.patch_check(before,after,arm,receipt)
    else:
        expected=bytearray(before);expected[offset:offset+4]=request['replacement'].to_bytes(4,'little',signed=True);require(expected==after,'Indexed payload/unrelated byte preservation')

def command(root,label,args):
    if len(args)>1 and str(args[1])=='snapshot':args=[*args,'--inventory','indexed-update']
    return allocation.rows.old_command(root,label,args)

def evaluate_checked(out):
    report=old_evaluate(out);initial=base.load_json(out/'preparation.json');revision=initial['source_revision']
    for scenario in inventory()['scenarios']:
        for role in base.ROLES:
            root=out/scenario['id']/role;snapshot=base.load_json(root/'rust/snapshot.json');observed=base.load_json(root/'dao-indexes.raw.json')
            require(observed['scenario_id']==scenario['id'] and observed['role']==role and observed['source_revision']==revision and observed['database_sha256']==base.write.digest(root/'database.mdb'),'Index observation identity')
            base.write.assert_indexes(observed['observations'],snapshot)
            probes=observed['extra_seeks'];queries=scenario.get('index_queries',[]);require([p['query'] for p in probes]==queries,'Declared extra Seek inventory')
            if queries:
                canonical_rows=next(t['rows'] for t in snapshot['tables'] if t['name']=='Items')
                for probe in probes:
                    matches=[r['values'] for r in canonical_rows if r['values']['Id']['value']==probe['query']]
                    require(probe['row'] in matches if matches else probe['row'] is None,'Extra Seek full-row result')
    report['index_observations_verified']=True;return report

base.inventory,base.recipe,base.check_preservation,base.command,base.evaluate_checked=inventory,recipe,check_preservation,command,evaluate_checked
if __name__=='__main__':base.main()
