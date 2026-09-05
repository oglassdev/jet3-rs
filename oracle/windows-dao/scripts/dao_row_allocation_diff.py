#!/usr/bin/env python3
"""Nine finite hosted allocation/compaction cases; frozen five-case runner unchanged."""
import copy
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('_allocation_rows',ROOT/'oracle/windows-dao/scripts/dao_row_update_diff.py')
rows=importlib.util.module_from_spec(spec);spec.loader.exec_module(rows)
base=rows.base
base.INVENTORY=ROOT/'oracle/windows-dao/protocol/v1_2/row-allocation-scenarios.json'
base.PLAN=ROOT/'oracle/windows-dao/acquisition/row-allocation-v1_2.plan.json'
spec=importlib.util.spec_from_file_location('_allocation_catalog',ROOT/'oracle/windows-dao/scripts/system_catalog.py')
catalog=importlib.util.module_from_spec(spec);spec.loader.exec_module(catalog)
IDS=rows.IDS+['DAO-UPDATE-'+v for v in ['EMPTY-EOF','FULL-EOF','MIDDLE-COMPACT','REPEATED-COMPACT']]

def require(value,message):
    if not value:raise base.ValidationError(message)

def recipe(scenario,role):
    request=scenario['request']
    if request.get('kind') not in ('eof_insert','compact_delete'):return rows.recipe(scenario,role)
    expected=copy.deepcopy(scenario);table,=[t for t in expected['tables'] if t['name']==request['table']]
    if role=='after':
        if request['kind']=='eof_insert':table['rows'].append(request['row'])
        else:
            for selected in request['selected_ids']:
                require(sum(r[0]==selected for r in table['rows'])==1,'Unique selected recipe row')
                table['rows']=[r for r in table['rows'] if r[0]!=selected]
    else:require(role=='before','Unknown role')
    return expected

def inventory():
    value=base.load_json(base.INVENTORY)
    require(value['document_type']=='dao_update_scenario_inventory' and value['protocol_version']=='1.2.0' and [s['id'] for s in value['scenarios']]==IDS,'Nine-case inventory')
    historical=base.load_json(ROOT/'oracle/windows-dao/protocol/v1_2/row-update-scenarios.json')['scenarios']
    require(value['scenarios'][:5]==historical,'Historical cases changed')
    for scenario in value['scenarios']:
        require(scenario['operation']==dict(mode='dao_open_rust_update',expected_outcome='success',error_class=None) and not set(scenario['required_branches'])-base.write.protocol.load_branch_ids() and scenario['coverage'],'Operation/coverage')
        recipe(scenario,'after')
    require(value['deferred_requirements'],'Missing limitations');return value

def table_rows(data):
    definition,_,objects=catalog._discover_catalog(data);name,ident=[catalog._ordinal(definition,n) for n in ('Name','Id')]
    roots=[r['values'][ident] for r in objects if r['values'][name]=='Items'];require(len(roots)==1,'Unique Items root')
    table=catalog._definition(data,roots[0]);pages,lval=catalog._table_pages(data,table);require(not lval,'Unexpected LVAL')
    return table,catalog._table_rows(data,table,pages)

def patch_images(before,scenario,table,records):
    request=scenario['request'];root=table['root'];definition=root*2048;expected=bytearray(before);steps=[]
    def word(offset,size=2):return int.from_bytes(expected[offset:offset+size],'little')
    def put(offset,value,size=2):expected[offset:offset+size]=value.to_bytes(size,'little')
    require(word(definition+12,4)==len(records),'Table count')
    if request['kind']=='eof_insert':
        require(len(before)%2048==0,'Source geometry');page=len(before)//2048;raw=rows.encoded(request['row']);image=bytearray(2048)
        image[:2]=b'\1\1';image[2:4]=(2036-len(raw)).to_bytes(2,'little');image[4:8]=root.to_bytes(4,'little');image[8:10]=b'\1\0';image[10:12]=(2048-len(raw)).to_bytes(2,'little');image[2048-len(raw):]=raw
        offsets=[]
        locators=[(1,0)]+[(word(definition+p,4)>>8,expected[definition+p]) for p in (35,39)]
        for role,(map_page,slot) in enumerate(locators):
            base=map_page*2048;start=word(base+10+2*slot);end=2048 if slot==0 else word(base+8+2*slot)
            require(10<=start<end<=2048 and expected[base+start]==0,'Inline map record')
            bit=page-word(base+start+1,4);offset=base+start+5+bit//8;require(bit>=0 and offset<base+end,'Map coverage');mask=1<<(bit%8)
            require(bool(expected[offset]&mask)==(role==0),'Prior map membership');expected[offset]=expected[offset]&~mask if role==0 else expected[offset]|mask;offsets.append(offset)
        put(definition+12,len(records)+1,4);expected.extend(image);steps.append(dict(page=page,slot=0,map_offsets=offsets))
    else:
        for selected in request['selected_ids']:
            record,=[r for r in records if r['values'][0]==selected];page,slot=record['page'],record['row'];base=page*2048;count=word(base+8)
            bounds=[2048]+[word(base+10+2*i)&0x1fff for i in range(count)];start,end=bounds[slot+1],bounds[slot];lowest=bounds[-1]
            require(count>=2 and lowest<=start<end<=2048 and word(base+2)==lowest-10-2*count,'Compaction bounds/free')
            require(expected[base+start:base+end]==rows.encoded(record['values']),'Selected raw row')
            width=end-start;expected[base+lowest+width:base+end]=expected[base+lowest:base+start]
            for ordinal in range(slot,count):
                old=word(base+10+2*ordinal);put(base+10+2*ordinal,(0xc000|end) if ordinal==slot else (old&0xe000)|((old&0x1fff)+width))
            put(base+2,word(base+2)+width);put(definition+12,word(definition+12,4)-1,4)
            steps.append(dict(selected_id=selected,page=page,slot=slot,start=start,end=end))
    return expected,dict(root=root,steps=steps)

def check_preservation(root,scenario,receipt):
    request=scenario['request']
    if request.get('kind') not in ('eof_insert','compact_delete'):return rows.check_preservation(root,scenario,receipt)
    require(receipt.get('scenario_id')==scenario['id'] and receipt.get('request')==request and receipt.get('preserved') is True,'Preservation request binding')
    before,after=[(root/role/'database.mdb').read_bytes() for role in base.ROLES]
    for role in base.ROLES:require(receipt[role+'_sha256']==base.write.digest(root/role/'database.mdb'),'Image identity')
    table,records=table_rows(before)
    wanted=next(t['rows'] for t in scenario['tables'] if t['name']=='Items')
    require(sorted(r['values'] for r in records)==sorted(wanted),'Original recipe rows')
    expected,coordinates=patch_images(before,scenario,table,records)
    require(all(receipt.get(k)==v for k,v in coordinates.items()),'Independent coordinates differ')
    require(expected==after,'Exact allocation/compaction or unrelated bytes differ')

def command(root,label,args):
    if len(args)>1 and str(args[1])=='snapshot':args=[*args,'--inventory','row-allocation']
    return rows.old_command(root,label,args)

base.inventory,base.recipe,base.check_preservation,base.command=inventory,recipe,check_preservation,command
if __name__=='__main__':base.main()
