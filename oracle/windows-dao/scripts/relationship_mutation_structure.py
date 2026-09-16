#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
import system_catalog as catalog
import numeric_index_mutation_structure as indexes
import allocation_lifecycle_structure as allocation
import relationship_create as relationships
for module in (catalog,indexes.catalog,allocation.catalog):
    module.MAX_PAGES=8192;module.MAX_ROWS_PER_PAGE=1019;module.MAX_TABLES=64;module.MAX_COLUMNS=255;module.MAX_TEXT=10000

def req(ok,msg):
    if not ok: raise ValueError(msg)
def sha(data): return hashlib.sha256(data).hexdigest()
def ident(path):
    data=path.read_bytes();return {'size':len(data),'sha256':sha(data)}
def byte_ident(data): return {'length':len(data),'sha256':sha(data)}
def pattern(length,seed): return bytes((i*37+seed*13+11)%256 for i in range(length))

def layout(raw,columns):
    count=raw[0];req(count==len(columns),'physical column count')
    pl=(count+7)//8;presence=raw[-pl:];variables=sum(c['storage']=='variable' for c in columns)
    fixed=1+max((c['fixed_offset']+c['size'] for c in columns if c['storage']=='fixed' and c['type']!='Boolean'),default=0)
    bounds=[];jumps=b''
    if variables:
        pos=len(raw)-pl-1;req(raw[pos]==variables,'variable count');jc=(len(raw)-1)//256;end=pos-jc-variables-1
        req(fixed<=end,'variable framing');lows=list(reversed(raw[end:end+variables+1]));jumps=raw[end+variables+1:pos]
        bounds=[low+256*sum(j!=255 and j<=i for j in jumps) for i,low in enumerate(lows)]
        if variables==255: bounds[-1]=end
        req(bounds[0]==fixed and bounds[-1]==end and all(a<=b for a,b in zip(bounds,bounds[1:])),'variable boundaries')
    else: end=len(raw)-pl;req(end==fixed,'fixed row body')
    vals=[]
    for c in columns:
        o=c['ordinal'];present=bool(presence[o//8]&(1<<(o%8)))
        if c['type']=='Boolean': value=b'' if present else None
        elif not present: value=None
        elif c['storage']=='fixed': value=raw[1+c['fixed_offset']:1+c['fixed_offset']+c['size']]
        else: value=raw[bounds[c['variable_index']]:bounds[c['variable_index']+1]]
        if not present and c['storage']=='variable':
            i=c['variable_index'];req(bounds[i]==bounds[i+1],'null variable bytes')
        vals.append(value)
    return vals,{'length':len(raw),'presence_hex':presence.hex(),'boundaries':bounds,'jumps_hex':jumps.hex()}

def map_info(data,locator,role):
    record,members=allocation.map_record(data,locator,role)
    return record,sorted(members)

def payload(data,field,owned,reached):
    req(len(field)>=12,'long header');word=int.from_bytes(field[:4],'little');length=word&0xffffff;flags=word&0xff000000
    # This acceptance matrix has one explicitly modeled 4,608-byte Notes value.
    req(field[8:12]==bytes(4) and length<=8192,'long header fields')
    descriptor={'raw_hex':field.hex(),'length':length,'flags':flags,'locator':None}
    if flags==0x80000000:
        req(field[4:8]==bytes(4) and len(field)==length+12,'inline length');return field[12:],descriptor
    req(flags in (0,0x40000000) and len(field)==12,'external descriptor')
    page,slot=int.from_bytes(field[5:8],'little'),field[4];descriptor['locator']={'page':page,'row':slot};out=bytearray()
    while page:
        req(page in owned and (page,slot) not in reached,'owned distinct payload');reached.add((page,slot))
        image=catalog._page(data,page,'payload');req(image[:2]==b'\x01\x01' and image[4:8]==b'LVAL','payload page')
        entry=next((e for e in catalog._row_directory(image,page) if e['row']==slot),None)
        req(entry is not None and not entry['hidden'] and not entry['overflow'],'active payload row')
        fragment=image[entry['start']:entry['end']]
        if flags==0x40000000: out.extend(fragment);break
        req(len(fragment)>4,'payload fragment');out.extend(fragment[4:]);req(len(out)<=length,'payload length bound')
        page,slot=int.from_bytes(fragment[1:4],'little'),fragment[0]
    req(len(out)==length,'complete payload');return bytes(out),descriptor

def rows(data,table,primary_column='Id'):
    cols=table['definition']['columns'];owned={g['column']:set(catalog._locator_pages(data,g['owned'],'lval owned')) for g in table['definition']['long_value_maps']};reached={o:set() for o in owned}
    result=[];physical=set();storage=set()
    for page_no in table['data_pages']:
        page=catalog._page(data,page_no,'data')
        for entry0 in catalog._row_directory(page,page_no):
            if entry0['start']<entry0['end'] and not entry0['overflow']: physical.add((page_no,entry0['row']))
            if entry0['hidden']: continue
            logical=(page_no,entry0['row']);current=page_no;entry=entry0;image=page;seen=set()
            while True:
                req((current,entry['row']) not in seen,'row cycle');seen.add((current,entry['row']));raw=image[entry['start']:entry['end']]
                if not entry['overflow']: break
                req(len(raw)==4,'overflow link');current,slot=int.from_bytes(raw[1:],'little'),raw[0];req(current in table['data_pages'],'overflow ownership');image=catalog._page(data,current,'overflow');entry=catalog._row_directory(image,current)[slot];req(entry['hidden'] and entry['start']<entry['end'],'overflow target')
            req((current,entry['row']) not in storage,'distinct storage');storage.add((current,entry['row']));rawvals,shape=layout(raw,cols);values={};descriptors={}
            for c,v in zip(cols,rawvals):
                if c['type']=='Boolean': decoded=v is not None
                elif v is None: decoded=None
                elif c['type'] in ('Memo','LongBinary'):
                    decoded,descriptor=payload(data,v,owned[c['ordinal']],reached[c['ordinal']]);descriptors[c['name']]=descriptor
                else: decoded=catalog._decode_value(c,v,True)
                values[c['name']]=decoded
            result.append({'locator':{'page':logical[0],'row':logical[1]},'storage':{'page':current,'row':entry['row']},'values':values,'raw_hex':raw.hex(),'shape':shape,'descriptors':descriptors})
    req(storage==physical,'every physical row reached')
    for ordinal,members in owned.items():
        active=set()
        for p in members:
            image=catalog._page(data,p,'lval')
            for e in catalog._row_directory(image,p):
                if e['hidden']: req(e['overflow'] and e['start']==e['end'],'payload tombstone')
                else: active.add((p,e['row']))
        req(active==reached[ordinal],'all active payload slots reached')
    req(len(result)==table['definition']['row_count'],'complete physical table row count')
    req(len({r['values'][primary_column] for r in result})==len(result),'distinct primary values')
    return sorted(result,key=lambda r:r['values'][primary_column])

def long_key(v,locator):
    suffix=locator['page'].to_bytes(3,'big')+bytes([locator['row']])
    return (b'\0' if v is None else b'\x7f'+((v&0xffffffff)^0x80000000).to_bytes(4,'big'))+suffix

def logical_relation(raw_hex):
    raw=bytes.fromhex(raw_hex);req(len(raw)==20 and raw[19]==2,'relationship record')
    return {'raw_hex':raw_hex,'selector':int.from_bytes(raw[:4],'little'),'physical_index':int.from_bytes(raw[4:8],'little'),'side':raw[8],'relation_ordinal':int.from_bytes(raw[9:13],'little'),'related_root':int.from_bytes(raw[13:17],'little'),'context_hex':raw[17:19].hex(),'class':raw[19]}

def observe(path,capture,expected):
    data=path.read_bytes();analysis=catalog.analyze_checkpoint(data);named={t['name']:t for t in analysis['tables'].values()}
    req(set(('Parent','Child','Notes Preserve','MSysRelationships'))<=set(named),'named tables')
    parent,child,notes=(named[n] for n in ('Parent','Child','Notes Preserve'))
    prows,crows,nrows=rows(data,parent),rows(data,child),rows(data,notes);req(len(nrows)==1,'one Notes row')
    stage=capture['stage'];ep,ec=expected
    req({r['values']['Id']:r['values']['Label'] for r in prows}==ep,f'{stage} parent raw values')
    actual_children={r['values']['Id']:[r['values']['ParentId'],r['values']['Body Memo'],r['values']['Blob OLE']] for r in crows};req(actual_children==ec,f'{stage} child raw values')
    nv=nrows[0]['values'];req(nv['Id']==7 and nv['Body Memo']==b'n'*4096 and nv['Blob OLE']==pattern(4096,0),f'{stage} Notes raw values')
    snap=capture['snapshot'];req(capture['status']=='pass' and capture['before']==capture['after'],f'{stage} read-only capture')
    req(snap['queries']==[] and set(snap['tables'])=={'Parent','Child','Notes Preserve','MSysObjects','MSysACEs','MSysQueries','MSysRelationships'},f'{stage} DAO table inventory')
    expected_fields={'parent':[('Id',4,4,1,False,False),('Label',10,32,2,False,False)],'child':[('Id',4,4,1,False,False),('ParentId',4,4,1,False,False),('Body Memo',12,0,2,False,False),('Blob OLE',11,0,2,False,False)]}
    for name in ('parent','child'):
      actual=[(f['name'],f['type'],f['size'],f['attributes'],f['required'],f['allow_zero_length']) for f in snap[name]['fields']];req(actual==expected_fields[name],f'{stage} {name} DAO fields')
    req(len(snap['parent']['rows'])==len(ep) and len(snap['child']['rows'])==len(ec),'complete DAO row inventory')
    req({r['id']:bytes.fromhex(r['label_hex']).decode('cp1252') for r in snap['parent']['rows']}==ep,f'{stage} DAO parents')
    dao_child={r['id']:[r['parent_id'],None if r['body_hex'] is None else bytes.fromhex(r['body_hex']),None if r['blob_hex'] is None else bytes.fromhex(r['blob_hex'])] for r in snap['child']['rows']};req(dao_child==ec,f'{stage} DAO children')
    req(snap['notes']=={'id':7,'body_length':4096,'body_sha256':sha(b'n'*4096),'blob_length':4096,'blob_sha256':sha(pattern(4096,0))},f'{stage} DAO Notes')
    req(snap['relations']==[{'name':'ParentChild','table':'Parent','foreign_table':'Child','attributes':0,'fields':[{'name':'Id','foreign_name':'ParentId'}]}],f'{stage} relation API')
    pidx=snap['parent']['indexes'];cidx=snap['child']['indexes'];req(len(pidx)==1 and pidx[0]['name']=='ByParent' and pidx[0]['primary'] and pidx[0]['unique'] and pidx[0]['required'] and not pidx[0]['foreign'],f'{stage} parent index API')
    req([(i['name'],i['primary'],i['unique'],i['required'],i['foreign'],i['ignore_nulls'],i['fields']) for i in cidx]==[('ByChild',True,True,True,False,False,[{'name':'Id','attributes':0}]),('ParentChild',False,False,False,True,False,[{'name':'ParentId','attributes':0}])],f'{stage} child index API')
    raw_by_name={'Parent':prows,'Child':crows};index_observations={}
    for name,table in (('Parent',parent),('Child',child)):
      phys=[]
      for idx in table['definition']['physical_indexes']:
        nodes,entries=indexes.tree(data,idx['root'],table['definition']['root'],[(4,False)])
        column=table['definition']['columns'][idx['keys'][0]['column']]['name'];expected_entries=sorted(long_key(r['values'][column],r['locator']) for r in raw_by_name[name]);req(entries==expected_entries,f'{stage} {name} index {idx["index"]} complete keys')
        phys.append({'index':idx['index'],'root':idx['root'],'flags':idx['flags'],'counter':idx['entry_count'],'first_word':int.from_bytes(bytes.fromhex(idx['prefix_hex']),'little'),'column':column,'nodes':nodes,'entries_hex':[e.hex() for e in entries]})
      index_observations[name]=phys
    # Every DAO traversal is complete and follows the physical key order. Seek must resolve exact values; ties may return either matching row.
    rawid={n:{(r['locator']['page'],r['locator']['row']):r['values']['Id'] for r in rs} for n,rs in raw_by_name.items()}
    for name,sname in (('Parent','parent'),('Child','child')):
      for physical in index_observations[name]:
        idxname='ByParent' if name=='Parent' else ('ByChild' if physical['index']==0 else 'ParentChild')
        traversed=[r['id'] for r in snap[sname]['index_reads'][idxname]['traversal']]
        order=[rawid[name][(int.from_bytes(bytes.fromhex(h)[-4:-1],'big'),bytes.fromhex(h)[-1])] for h in physical['entries_hex']]
        req(traversed==order,f'{stage} {idxname} traversal')
        complete={row['id']:row for row in snap[sname]['rows']}
        req(all(row==complete[row['id']] for row in snap[sname]['index_reads'][idxname]['traversal']),f'{stage} {idxname} traversal complete fields')
        for seek in snap[sname]['index_reads'][idxname]['seek']:
          q=seek['query'];row=seek['row'];matches=[r for r in raw_by_name[name] if r['values'][physical['column']]==q]
          req((row is None)==(not matches),f'{stage} {idxname} seek presence {q}')
          if row is not None:
            req(row['id'] in {r['values']['Id'] for r in matches},f'{stage} {idxname} seek value {q}')
            req(row==complete[row['id']],f'{stage} {idxname} seek complete fields {q}')
    # Exact catalog row and reciprocal logical records.
    relrows=catalog._generic_rows(analysis['system_rows']['MSysRelationships'],analysis);wanted=[{'szRelationship':'ParentChild','grbit':0,'ccolumn':1,'icolumn':0,'szObject':'Child','szColumn':'ParentId','szReferencedObject':'Parent','szReferencedColumn':'Id'}];req(relrows==wanted,f'{stage} MSysRelationships row')
    relation_records={}
    for name,table,side,other in (('Parent',parent,1,child),('Child',child,2,parent)):
      rel=[logical_relation(i['raw_hex']) for i in table['definition']['logical_indexes'] if i['class']==2];req(len(rel)==1,f'{stage} {name} reciprocal count');rr=rel[0];req(rr['side']==side and rr['related_root']==other['definition']['root'] and rr['context_hex']=='0000',f'{stage} {name} reciprocal fields');relation_records[name]=rr
    req(relation_records['Parent']['selector']==relation_records['Child']['relation_ordinal'] and relation_records['Parent']['relation_ordinal']==relation_records['Child']['selector'],f'{stage} selector reciprocity')
    req(relation_records['Parent']['physical_index']==0 and relation_records['Child']['physical_index']==1,f'{stage} physical relation indexes')
    # System relationship indexes retain exact raw keys all pointing to the sole catalog row.
    sysrel=named['MSysRelationships'];sysidx=[]
    req(sysrel['definition']['row_count']==len(relrows)==1,'complete relationship catalog row count')
    for idx in sysrel['definition']['physical_indexes']:
      leaf=relationships.leaf_entries(data,idx['root']);req(len(leaf['entries'])==1,'system relation index cardinality');e=leaf['entries'][0];req((e['row_page'],e['row'])==(sysrel['data_pages'][0],0),'system relation key locator');sysidx.append({'index':idx['index'],'root':idx['root'],'flags':idx['flags'],'counter':idx['entry_count'],'first_word':int.from_bytes(bytes.fromhex(idx['prefix_hex']),'little'),'key':e})
    maps={'global':map_info(data,{'page':1,'row':0},'global')}
    for name,table in named.items():
      d=table['definition']
      for role,loc in d['maps'].items(): maps[f'{name}/table/{role}']=map_info(data,loc,f'{name}-{role}')
      for idx in d['physical_indexes']: maps[f'{name}/index/{idx["index"]}']=map_info(data,idx['map'],f'{name}-index-{idx["index"]}')
      for group in d['long_value_maps']:
       for role in ('owned','available'): maps[f'{name}/lval/{group["column"]}/{role}']=map_info(data,group[role],f'{name}-lval-{group["column"]}-{role}')
    claimed={};free=set(analysis['free_pages']);metadata={0,1}
    locators=[(record['locator']['page'],record['locator']['row']) for record,members in maps.values()]
    references=[page for record,members in maps.values() for page in record['references'] if page]
    req(len(locators)==len(set(locators)) and len(references)==len(set(references)),f'{stage} distinct map rows and bitmap pages')
    for name,table in named.items():
      d=table['definition'];owned=set(maps[f'{name}/table/owned'][1]);available=set(maps[f'{name}/table/available'][1])
      req(available<=owned and not owned&free,f'{stage} {name} data allocation')
      metadata.update(d['pages'])
      groups=[(f'{name}/table',owned)]
      for idx in d['physical_indexes']:
        members=set(maps[f'{name}/index/{idx["index"]}'][1]);groups.append((f'{name}/index/{idx["index"]}',members))
        req(not members&free,f'{stage} live index pages globally allocated')
        if name in index_observations:
          nodes=index_observations[name][idx['index']]['nodes']
          req({node['page'] for node in nodes}<=members,f'{stage} every index node owned')
      for group in d['long_value_maps']:
        key=f'{name}/lval/{group["column"]}';members=set(maps[key+'/owned'][1]);groups.append((key,members))
        req(set(maps[key+'/available'][1])<=members and not members&free,f'{stage} live payload allocation')
      for role,members in groups:
        for page in members:
          req(page not in claimed,f'{stage} unique page ownership {page}');claimed[page]=role
    for record,members in maps.values():
      metadata.add(record['locator']['page']);metadata.update(p for p in record['references'] if p)
    req(not metadata&free and not metadata&set(claimed),f'{stage} metadata allocation')
    note_pages={notes['definition']['root'],*notes['data_pages']}
    for role,loc in notes['definition']['maps'].items(): note_pages.add(loc['page']);note_pages.update(catalog._locator_pages(data,loc,'notes map'))
    for group in notes['definition']['long_value_maps']:
      for role in ('owned','available'): note_pages.add(group[role]['page']);note_pages.update(catalog._locator_pages(data,group[role],f'notes lval {role}'))
    note_hashes={str(p):sha(catalog._page(data,p,'note-owned')) for p in sorted(note_pages)}
    def compact(rs):
      return [{'id':r['values']['Id'],'locator':r['locator'],'storage':r['storage'],'raw_sha256':sha(bytes.fromhex(r['raw_hex'])),'values':{k:(v if not isinstance(v,bytes) else byte_ident(v)) for k,v in r['values'].items()},'descriptors':r['descriptors']} for r in rs]
    dao_schema={name:{'attributes':snap[name]['attributes'],'fields':snap[name]['fields'],'indexes':snap[name]['indexes']} for name in ('parent','child')}
    raw_schema={name:{'root':named[name]['definition']['root'],'columns':named[name]['definition']['columns'],'maps':named[name]['definition']['maps'],'logical_indexes':named[name]['definition']['logical_indexes'],'physical_indexes':[{k:v for k,v in index.items() if k not in ('entry_count','entry_count_offset','prefix_hex')} for index in named[name]['definition']['physical_indexes']],'long_value_maps':named[name]['definition']['long_value_maps']} for name in ('Parent','Child','Notes Preserve','MSysRelationships')}
    return {'file':path.name,'identity':ident(path),'page_count':len(data)//2048,'page0_counter':data[1538],'page0_sha256':sha(data[:2048]),'free_pages':analysis['free_pages'],'table_roots':{n:t['definition']['root'] for n,t in named.items()},'dao_schema':dao_schema,'raw_schema':raw_schema,'rows':{'Parent':compact(prows),'Child':compact(crows),'Notes Preserve':compact(nrows)},'indexes':index_observations,'relationship_records':relation_records,'relationship_catalog_row':relrows[0],'relationship_system_indexes':sysidx,'maps':{k:{'record':v[0],'members':v[1]} for k,v in maps.items()},'notes_page_hashes':note_hashes}
