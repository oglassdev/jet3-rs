#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import system_catalog as catalog
import relationship_mutation_structure as structure
import numeric_index_mutation_structure as indexes
import allocation_lifecycle_structure as allocation
for m in (catalog,indexes.catalog,allocation.catalog):
    m.MAX_PAGES=8192;m.MAX_ROWS_PER_PAGE=1019;m.MAX_TABLES=64;m.MAX_COLUMNS=255;m.MAX_TEXT=10000

def req(x,msg):
    if not x: raise ValueError(msg)
def sha(b): return hashlib.sha256(b).hexdigest()
def ident(p):
    b=p.read_bytes();return {'size':len(b),'sha256':sha(b)}
def find_properties(data,table_name):
    definition,pages,decoded=catalog._discover_catalog(data)
    no=catalog._ordinal(definition,'Name');po=catalog._ordinal(definition,'LvProp')
    group=next(g for g in definition['long_value_maps'] if g['column']==po)
    owned=set(catalog._locator_pages(data,group['owned'],'LvProp owned'))
    hits=[]
    for row in decoded:
        if row['values'][no]!=table_name:continue
        image=catalog._page(data,row['page'],'catalog')
        entry=catalog._row_directory(image,row['page'])[row['row']]
        vals,_=structure.layout(image[entry['start']:entry['end']],definition['columns'])
        if vals[po] is None:return None
        reached=set();payload,desc=structure.payload(data,vals[po],owned,reached)
        hits.append({'payload':payload,'descriptor':desc,'fragments':sorted([{'page':p,'row':r} for p,r in reached],key=lambda x:(x['page'],x['row']))})
    req(len(hits)==1,f'{table_name}: catalog row inventory')
    return hits[0]
def expected_properties(fields):
    out=bytearray.fromhex('4b4b4400210000008000080052657175697265640f00416c6c6f775a65726f4c656e677468')
    for f in fields:
        if f.get('attributes')==16: continue
        name=f['name'].encode('cp1252');records=bytearray()
        if f['type'] in (10,12): records += bytes.fromhex('0900010101000100')+bytes([255 if f['allow_zero_length'] else 0])
        records += bytes.fromhex('090001010000010000')
        out += (12+len(name)+len(records)).to_bytes(4,'little')+b'\x01\x00'+(6+len(name)).to_bytes(4,'little')+len(name).to_bytes(2,'little')+name+records
    return bytes(out)
def map_record(data,loc,role):
    rec,members=allocation.map_record(data,loc,role)
    return {'locator':loc,'record':rec,'members':sorted(members)}
