#!/usr/bin/env python3
"""Independent ownership and unchanged metadata checks for graph mutations."""
import hashlib
import system_catalog as c
import relationship_mutation_structure as s
import allocation_lifecycle_structure as a
import numeric_index_mutation_structure as ix
import rich_relationship_structure as ca
for module in (c, ix.catalog, a.catalog):
    module.MAX_PAGES = 8192
    module.MAX_ROWS_PER_PAGE = 1019
    module.MAX_TABLES = 64
    module.MAX_COLUMNS = 255
    module.MAX_TEXT = 10000
def check(p):
 d=p.read_bytes();an=c.analyze_checkpoint(d);free=set(an['free_pages']);named={t['name']:t for t in an['tables'].values()};claimed={};metadata={0,1};payloads=0
 gm,gmembers=a.map_record(d,{'page':1,'row':0},'global');assert set(gmembers)==free
 for name,t in named.items():
  if name.startswith('MSys'):continue
  de=t['definition'];metadata.update(de['pages']);locs=[]
  for role,loc in de['maps'].items():locs.append(loc)
  for q in de['physical_indexes']:locs.append(q['map'])
  for g in de['long_value_maps']:locs.extend((g['owned'],g['available']))
  for loc in locs:
   rec,mem=a.map_record(d,loc,name);metadata.add(loc['page']);metadata.update(x for x in rec['references'] if x)
  groups=[('table',set(a.map_record(d,de['maps']['owned'],name)[1]))]
  for q in de['physical_indexes']:groups.append((f'index{q["index"]}',set(a.map_record(d,q['map'],name)[1])))
  rows=s.rows(d,t)
  for g in de['long_value_maps']:
   owned=set(a.map_record(d,g['owned'],name)[1]);groups.append((g['column_name'],owned));reached=set()
   for row in rows:
    desc=row['descriptors'].get(g['column_name'])
    if desc is None:continue
    local=set();payload,_=s.payload(d,bytes.fromhex(desc['raw_hex']),owned,local);assert payload==row['values'][g['column_name']];reached.update(local);payloads+=1
   active=set()
   for page in owned:
    image=c._page(d,page,name)
    for e in c._row_directory(image,page):
     if not e['hidden']:active.add((page,e['row']))
   assert reached==active,(p,name,g['column_name'],reached,active)
  for role,members in groups:
   assert not members&free
   for page in members:assert page not in claimed,(p,page,claimed[page],name+'/'+role);claimed[page]=name+'/'+role
 assert not metadata&free and not metadata&set(claimed)
 return payloads

def meta(p):
 d=p.read_bytes();an=c.analyze_checkpoint(d);out={}
 for t in an['tables'].values():
  if t['name'].startswith('MSys'):continue
  de=t['definition'];prop=ca.find_properties(d,t['name']);out[t['name']]={'columns':de['columns'],'logical_indexes':de['logical_indexes'],'properties':None if prop is None else prop['payload'].hex()}
 return out

def pages(p):
 d=p.read_bytes();an=c.analyze_checkpoint(d);named={t['name']:t for t in an['tables'].values()};out={}
 for name in ('MSysObjects','MSysRelationships','MSysQueries','MSysACEs'):
  t=named[name];de=t['definition'];ps=set(de['pages'])|set(t['data_pages']);locs=list(de['maps'].values())+[q['map'] for q in de['physical_indexes']]
  for group in de['long_value_maps']:locs.extend((group['owned'],group['available']))
  for loc in locs:
   rec,mem=a.map_record(d,loc,name);ps.add(loc['page']);ps.update(x for x in rec['references'] if x);ps.update(mem)
  out[name]={x:hashlib.sha256(c._page(d,x,name)).hexdigest() for x in ps}
 return out
