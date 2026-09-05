#!/usr/bin/env python3
"""Five-case hosted row-mutation successor, reusing the frozen update evaluator."""
import copy
import importlib.util
from pathlib import Path

# An isolated module instance supplies the unchanged identity/provider/coverage/
# acquisition gates; configuring it never changes the historical three-case runner.
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('_row_update_base', ROOT / 'oracle/windows-dao/scripts/dao_update_diff.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.INVENTORY = ROOT / 'oracle/windows-dao/protocol/v1_2/row-update-scenarios.json'
base.PLAN = ROOT / 'oracle/windows-dao/acquisition/row-update-v1_2.plan.json'
old_recipe, old_check, old_command, old_evaluate = base.recipe, base.check_preservation, base.command, base.evaluate
IDS = ['DAO-UPDATE-FIRST-FIELD','DAO-UPDATE-LATER-ROW','DAO-UPDATE-LATER-TABLE','DAO-UPDATE-INSERT-ROW','DAO-UPDATE-DELETE-TAIL']


def recipe(scenario, role):
    request = scenario['request']
    if 'kind' not in request: return old_recipe(scenario, role)
    expected = copy.deepcopy(scenario)
    table, = [t for t in expected['tables'] if t['name'] == request['table']]
    if role == 'after':
        if request['kind'] == 'insert': table['rows'].append(request['row'])
        else: table['rows'] = [r for r in table['rows'] if r[0] != request['selected_id']]
    elif role != 'before': raise base.ValidationError('Unknown role')
    return expected


def inventory():
    value = base.load_json(base.INVENTORY)
    if value['document_type'] != 'dao_update_scenario_inventory' or value['protocol_version'] != '1.2.0' or [s['id'] for s in value['scenarios']] != IDS:
        raise base.ValidationError('Wrong five-case inventory')
    historical = base.load_json(ROOT / 'oracle/windows-dao/protocol/v1_2/update-scenarios.json')['scenarios']
    if value['scenarios'][:3] != historical: raise base.ValidationError('Historical cases changed')
    for scenario in value['scenarios']:
        if (scenario['operation'] != dict(mode='dao_open_rust_update',expected_outcome='success',error_class=None)
                or set(scenario['required_branches']) - base.write.protocol.load_branch_ids() or not scenario['coverage']):
            raise base.ValidationError('Invalid operation/coverage')
        recipe(scenario, 'after')
    if value['scenarios'][3]['request'] != dict(kind='insert',table='Items',row=[88,-8800,'inserted']) or value['scenarios'][4]['request'] != dict(kind='delete',table='Items',selected_id=3):
        raise base.ValidationError('Wrong finite row requests')
    if not value['deferred_requirements']: raise base.ValidationError('Missing limitations')
    return value


def encoded(row):
    ident, value, text = row; payload = text.encode('ascii')
    if len(payload)>246: raise base.ValidationError('Narrow finite Text row required')
    return bytes([3])+ident.to_bytes(4,'little',signed=True)+value.to_bytes(4,'little',signed=True)+payload+bytes([9+len(payload),9,1,7])


def check_preservation(root, scenario, receipt):
    request=scenario['request']
    if 'kind' not in request: return old_check(root,scenario,receipt)
    if receipt.get('scenario_id')!=scenario['id'] or receipt.get('request')!=request or receipt.get('preserved') is not True:
        raise base.ValidationError('Independent row verification request mismatch')
    before,after=[(root/role/'database.mdb').read_bytes() for role in base.ROLES]
    for role in base.ROLES:
        if receipt[role+'_sha256']!=base.write.digest(root/role/'database.mdb'): raise base.ValidationError('Image identity')
    names=['root','page','slot','row_offset','row_length']
    if any(type(receipt[n]) is not int or receipt[n]<0 for n in names): raise base.ValidationError('Row coordinates')
    page=receipt['page']*2048;definition=receipt['root']*2048;slot=receipt['slot'];start=receipt['row_offset'];length=receipt['row_length']
    if len(before)!=len(after) or page+2048>len(before) or definition+2048>len(before) or start<page+10 or start+length>page+2048:
        raise base.ValidationError('Row image bounds')
    count=int.from_bytes(before[page+8:page+10],'little');free=int.from_bytes(before[page+2:page+4],'little')
    rows=next(t['rows'] for t in scenario['tables'] if t['name']==request['table'])
    if int.from_bytes(before[definition+12:definition+16],'little')!=len(rows): raise base.ValidationError('Table count')
    expected=bytearray(before); patches=[]
    def put(offset,value):
        patches.append(dict(offset=offset,before=list(expected[offset:offset+len(value)]),after=list(value)))
        expected[offset:offset+len(value)]=value
    if request['kind']=='insert':
        row=encoded(request['row'])
        if slot!=count or length!=len(row) or not 0<count<255: raise base.ValidationError('Insert slot/width')
        packed=int.from_bytes(before[page+8+2*count:page+10+2*count],'little')&0x1fff
        if start+length!=page+packed or free!=packed-10-2*count: raise base.ValidationError('Insert contiguous space')
        put(start,row);put(page+10+2*slot,(start-page).to_bytes(2,'little'));put(page+8,(count+1).to_bytes(2,'little'))
        put(page+2,(free-length-2).to_bytes(2,'little'));put(definition+12,(len(rows)+1).to_bytes(4,'little'))
    else:
        selected,=[r for r in rows if r[0]==request['selected_id']]
        if slot+1!=count or count<2 or before[start:start+length]!=encoded(selected) or free!=start-page-10-2*count:
            raise base.ValidationError('Delete row/tail/free span')
        put(page+10+2*slot,(0xc000|start+length-page).to_bytes(2,'little'));put(page+2,(free+length).to_bytes(2,'little'));put(definition+12,(len(rows)-1).to_bytes(4,'little'))
    if patches!=receipt['patches'] or expected!=after: raise base.ValidationError('Exact row patch/unrelated byte mismatch')


def command(root,label,args):
    if len(args)>1 and str(args[1])=='snapshot': args=[*args,'--inventory','row-update']
    return old_command(root,label,args)


def evaluate(out):
    # Both dispatch and retained-data analysis require the successor's own pins.
    plan_hash=base.write.digest(base.PLAN)
    base.validate_plan(plan_hash)
    old_evaluate(out)
    report=base.load_json(out/'report.json');report['plan_sha256']=plan_hash
    base.common.write_canonical(out/'report.json',report)


base.inventory,base.recipe,base.check_preservation,base.command,base.evaluate=inventory,recipe,check_preservation,command,evaluate
if __name__=='__main__':base.main()
