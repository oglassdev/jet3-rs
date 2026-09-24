#!/usr/bin/env python3
"""EXP-0273/0274 independent graph rows, indexes and relationship decoding."""
import copy
import hashlib
import system_catalog as catalog
import relationship_mutation_structure as structure
import numeric_index_mutation_structure as indexes
import dao_common as relcreate
import allocation_lifecycle_structure as allocation
for module in (catalog, indexes.catalog, allocation.catalog):
    module.MAX_PAGES = 8192
    module.MAX_ROWS_PER_PAGE = 1019
    module.MAX_TABLES = 64
    module.MAX_COLUMNS = 255
    module.MAX_TEXT = 10000
def req(x,m):
 if not x:raise ValueError(m)
def ident(p):
 b=p.read_bytes();return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def norm(v):return v.hex() if isinstance(v,bytes) else v.encode('cp1252').hex() if isinstance(v,str) else v
def expected_rows(model,t):return [{k:norm(v) for k,v in r.items()} for r in sorted(model[t],key=lambda x:x['Id'])]
def apply(model,op):
 rows=model[op['table']]
 if op['kind']=='insert':rows.append(copy.deepcopy(op['values']))
 elif op['kind']=='delete':rows[:]=[r for r in rows if r['Id']!=op['id']]
 else:next(r for r in rows if r['Id']==op['id'])[op['column']]=op['value']
def mapinfo(data,loc,label):
 rec,members=allocation.map_record(data,loc,label);return {'locator':loc,'record':rec,'members':sorted(members)}
def observe(path,capture,case,model,label):
 data=path.read_bytes();req(capture['before']==capture['after']==ident(path),label+' identity');snap=capture['snapshot'];specs={t['name']:t for t in case['tables']};expected_tables=set(specs)|{'MSysObjects','MSysACEs','MSysQueries','MSysRelationships'}
 req(set(snap['tables'])==expected_tables and snap['queries']==[],label+' inventory');req(len(snap['user_tables'])==len(specs),label+' DAO tables')
 dao={t['name']:t for t in snap['user_tables']};analysis=catalog.analyze_checkpoint(data);named={t['name']:t for t in analysis['tables'].values()};req(expected_tables<=set(named),label+' raw tables')
 rawrows={};phys={};logical_rel={};maps={}
 for tn,spec in specs.items():
  want=expected_rows(model,tn);req(dao[tn]['rows']==want,label+' DAO rows '+tn)
  rows=structure.rows(data,named[tn]);got=[{k:norm(v) for k,v in r['values'].items()} for r in rows];req(got==want,label+' raw rows '+tn);rawrows[tn]=[{'locator':r['locator'],'values':got[i],'raw_hex':r['raw_hex'],'storage':r['storage']} for i,r in enumerate(rows)]
  d=named[tn]['definition'];ordinary={x['physical_index']:x['name'] for x in d['logical_indexes'] if x['class'] in (0,1)};rels=[]
  for x in d['logical_indexes']:
   if x['class']==2:
    q=structure.logical_relation(x['raw_hex']);q['name']=x['name'];q['raw_hex']=x['raw_hex'];rels.append(q)
    if q['side']==2:ordinary[q['physical_index']]=x['name']
  logical_rel[tn]=rels;byloc={(r['locator']['page'],r['locator']['row']):r for r in rows};items=[]
  for ordinal,ix in enumerate(d['physical_indexes']):
   req(len(ix['keys'])==1 and ix['keys'][0]['direction']==1,label+' Long ascending key');col=d['columns'][ix['keys'][0]['column']]['name'];nodes,entries=indexes.tree(data,ix['root'],d['root'],[(4,False)]);wanted=sorted(structure.long_key(r['values'][col],r['locator']) for r in rows);req(entries==wanted,label+' keys '+tn+'/'+str(ordinal));name=ordinary[ordinal];read=dao[tn]['index_reads'][name];req(read['field']==col,label+' DAO index field');order=[byloc[(int.from_bytes(e[-4:-1],'big'),e[-1])]['values']['Id'] for e in entries];req([r['Id'] for r in read['traversal']]==order,label+' traversal '+tn+'/'+name);queries=sorted({r['values'][col] for r in rows if r['values'][col] is not None})+[999999];req([x['query'] for x in read['seek']]==queries,label+' seeks '+tn+'/'+name)
   items.append({'ordinal':ordinal,'name':name,'column':col,'flags':ix['flags'],'first_word':int.from_bytes(bytes.fromhex(ix['prefix_hex']),'little'),'second_word':ix['entry_count'],'root':ix['root'],'nodes':[n['page'] for n in nodes],'entries_hex':[e.hex() for e in entries]})
  phys[tn]=items
  for role,loc in d['maps'].items():maps[f'{tn}/table/{role}']=mapinfo(data,loc,label)
  for ix in d['physical_indexes']:maps[f'{tn}/index/{ix["index"]}']=mapinfo(data,ix['map'],label)
  for g in d['long_value_maps']:
   for role in ('owned','available'):maps[f'{tn}/lval/{g["column_name"]}/{role}']=mapinfo(data,g[role],label)
 # Pair every declared relationship using reciprocal selector/ordinal cross equality.
 pairs=[]
 for rs in case['relations']:
  parent=named[rs['table']]['definition'];child=named[rs['foreign_table']]['definition'];children=[r for r in logical_rel[rs['foreign_table']] if r['side']==2 and r['name']==rs['name'] and r['related_root']==parent['root']];req(len(children)==1,label+' child reciprocal '+rs['name']);cr=children[0]
  parents=[r for r in logical_rel[rs['table']] if r['side']==1 and r['related_root']==child['root'] and r['selector']==cr['relation_ordinal'] and r['relation_ordinal']==cr['selector']];req(len(parents)==1,label+' parent reciprocal '+rs['name']);pr=parents[0];req(pr['context_hex']==cr['context_hex']=='0000',label+' context '+rs['name']);foreign=phys[rs['foreign_table']][cr['physical_index']];req(foreign['column']==rs['foreign_field'] and foreign['flags']==0,label+' foreign index '+rs['name']);pairs.append({'spec':rs,'parent':pr,'child':cr,'foreign_index':foreign})
 relrows=catalog._generic_rows(analysis['system_rows']['MSysRelationships'],analysis);wanted=[{'szRelationship':r['name'],'grbit':0,'ccolumn':1,'icolumn':0,'szObject':r['foreign_table'],'szColumn':r['foreign_field'],'szReferencedObject':r['table'],'szReferencedColumn':r['field']} for r in case['relations']];req(relrows==wanted,label+' relationship rows')
 objects=catalog._generic_rows(analysis['system_rows']['MSysObjects'],analysis);robj=[o for o in objects if o['Type']==8];req([(o['Id'],o['ParentId'],o['Name'],o['Flags'],o['Owner'],o['LvProp']) for o in robj]==[(-2147483648+i,251658243,r['name'],0,'0301',None) for i,r in enumerate(case['relations'])],label+' relationship objects')
 aces=catalog._generic_rows(analysis['system_rows']['MSysACEs'],analysis);race=[a for a in aces if a['ObjectId'] in {o['Id'] for o in robj}];req(race==[x for i in range(len(robj)) for x in ({'ObjectId':-2147483648+i,'SID':'0301','ACM':983294,'FInheritable':False},{'ObjectId':-2147483648+i,'SID':'0201','ACM':1048575,'FInheritable':False})],label+' relationship ACEs')
 sr=named['MSysRelationships'];sysidx=[];rawloc={(r['page'],r['row']) for r in analysis['system_rows']['MSysRelationships']['rows']}
 for ix in sr['definition']['physical_indexes']:
  leaf=relcreate.leaf_entries(data,ix['root']);req(len(leaf['entries'])==len(wanted),label+' system index count');req({(e['row_page'],e['row']) for e in leaf['entries']}==rawloc,label+' system index locators');sysidx.append({'ordinal':ix['index'],'flags':ix['flags'],'first_word':int.from_bytes(bytes.fromhex(ix['prefix_hex']),'little'),'second_word':ix['entry_count'],'entries':leaf['entries']})
 return {'identity':ident(path),'page0_relationship_byte':data[1538],'rows':rawrows,'physical_indexes':phys,'logical_relationships':logical_rel,'pairs':pairs,'relationship_rows':relrows,'relationship_objects':robj,'relationship_aces':race,'system_relationship_indexes':sysidx,'maps':maps}
