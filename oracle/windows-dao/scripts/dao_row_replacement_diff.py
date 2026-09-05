#!/usr/bin/env python3
"""Seventeen-case hosted update adapter; frozen indexed sidecar gates retained."""
import copy
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
def private(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'oracle/windows-dao/scripts'/file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
indexed=private('_replacement_indexed','dao_indexed_update_diff.py');base=indexed.base
release=private('_replacement_release','row_delete_release.py')
replacement=private('_replacement_rows','row_update_candidate.py')
base.INVENTORY=ROOT/'oracle/windows-dao/protocol/v1_2/row-replacement-scenarios.json'
base.PLAN=ROOT/'oracle/windows-dao/acquisition/row-replacement-v1_2.plan.json'
IDS=indexed.IDS+['DAO-UPDATE-'+n for n in ('SOLE-RELEASE','ROW-GROW','ROW-SHRINK','ROW-LATER-TOMBSTONE')]
require=indexed.require

def recipe(scenario,role):
    request=scenario['request'];kind=request.get('kind')
    if kind not in ('sole_release','row_replace'):return indexed.recipe(scenario,role)
    result=copy.deepcopy(scenario);table,=[t for t in result['tables'] if t['name']==request['table']]
    require(sum(r[0]==request['selected_id'] for r in table['rows'])==1,'Unique requested row')
    if role=='after':
        table['rows']=[r for r in table['rows'] if r[0]!=request['selected_id']] if kind=='sole_release' else [request['replacement'] if r[0]==request['selected_id'] else r for r in table['rows']]
    else:require(role=='before','Unknown role')
    return result

def inventory():
    value=base.load_json(base.INVENTORY)
    require(value['document_type']=='dao_update_scenario_inventory' and value['protocol_version']=='1.2.0' and [s['id'] for s in value['scenarios']]==IDS,'Seventeen-case inventory')
    historical=base.load_json(ROOT/'oracle/windows-dao/protocol/v1_2/indexed-update-scenarios.json')['scenarios'];require(value['scenarios'][:13]==historical,'Historical indexed cases changed')
    for scenario in value['scenarios']:
        require(scenario['operation']==dict(mode='dao_open_rust_update',expected_outcome='success',error_class=None) and not set(scenario['required_branches'])-base.write.protocol.load_branch_ids() and scenario['coverage'],'Operation/coverage');recipe(scenario,'after')
    require(value['deferred_requirements'],'Missing limitations');return value

def check_preservation(root,scenario,receipt):
    request=scenario['request'];kind=request.get('kind')
    if kind not in ('sole_release','row_replace'):return indexed.check_preservation(root,scenario,receipt)
    require(receipt.get('scenario_id')==scenario['id'] and receipt.get('request')==request and receipt.get('preserved') is True,'Replacement request binding')
    before,after=[(root/role/'database.mdb').read_bytes() for role in base.ROLES]
    for role in base.ROLES:require(receipt[role+'_sha256']==base.write.digest(root/role/'database.mdb'),'Replacement image identity')
    if kind=='sole_release':release.patch_check(before,after,request,receipt['locator'])
    else:
        arm=copy.deepcopy(request);binary=arm['replacement'][3]
        if binary is not None:arm['replacement'][3]=binary.encode('ascii').hex()
        replacement.patch_check(before,after,arm,receipt['locator'])

def command(root,label,args):
    if len(args)>1 and str(args[1])=='snapshot':args=[*args,'--inventory','row-replacement']
    return indexed.allocation.rows.old_command(root,label,args)

# The indexed evaluator directly reads its module's inventory for every sidecar;
# make that same complete inventory visible without changing any frozen source.
indexed.inventory=inventory
base.inventory,base.recipe,base.check_preservation,base.command=inventory,recipe,check_preservation,command
if __name__=='__main__':base.main()
