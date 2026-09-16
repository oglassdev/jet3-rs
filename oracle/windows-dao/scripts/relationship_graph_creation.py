#!/usr/bin/env python3
import argparse,copy,hashlib,json
import relationship_graph_checks as checks
from pathlib import Path

def ident(p):
 b=p.read_bytes();return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def req(v,m):
 if not v:raise ValueError(m)
def flatten(v):
 while isinstance(v,list) and len(v)==1 and isinstance(v[0],list):v=v[0]
 return v
def stable_properties(v):
 out=[]
 for p in flatten(v):
  q=dict(p)
  if q.get('name') in ('DateCreated','LastUpdated'):q['value']='<volatile-date>'
  out.append(q)
 return out
def resolve_auto(case):
 case=copy.deepcopy(case)
 for table in case['tables']:
  auto=next((f['name'] for f in table['fields'] if f.get('attributes')==16),None)
  if auto:
   value=1
   for row in table['rows']:
    if row.get(auto) is None:row[auto]=value;value+=1
    else:value=max(value,row[auto]+1)
  for row in table['rows']:
   for field in table['fields']:
    value=row.get(field['name'])
    if not isinstance(value,dict):continue
    if value['kind']=='repeat':row[field['name']]=bytes([value['byte']])*value['length']
    elif value['kind']=='pattern':row[field['name']]=bytes((i*37+value['seed']*13+11)%256 for i in range(value['length']))
    else:raise ValueError('unknown payload recipe')
 return case
def expected_properties(fields):
 out=bytearray.fromhex('4b4b4400210000008000080052657175697265640f00416c6c6f775a65726f4c656e677468')
 for f in fields:
  if f.get('attributes')==16:continue
  name=f['name'].encode('cp1252');records=bytearray()
  if f['type'] in (10,12):records+=bytes.fromhex('0900010101000100')+bytes([255 if f.get('allow_zero_length',True) else 0])
  records+=bytes.fromhex('090001010000010000')
  out+=(12+len(name)+len(records)).to_bytes(4,'little')+b'\x01\x00'+(6+len(name)).to_bytes(4,'little')+len(name).to_bytes(2,'little')+name+records
 return bytes(out)
def decode_properties(payload):
 req(payload[:4]==b'KKD\0','property magic');dictionary_length=int.from_bytes(payload[4:8],'little');end=4+dictionary_length;req(10<=end<=len(payload) and payload[8:10]==b'\x80\0','property dictionary framing');names=[];cursor=10
 while cursor<end:
  req(cursor+2<=end,'property dictionary name length');size=int.from_bytes(payload[cursor:cursor+2],'little');cursor+=2;req(cursor+size<=end,'property dictionary name');names.append(payload[cursor:cursor+size].decode('cp1252'));cursor+=size
 req(cursor==end and len(names)==len(set(names)),'property dictionary inventory');blocks=[]
 while cursor<len(payload):
  req(cursor+12<=len(payload),'property block header');length=int.from_bytes(payload[cursor:cursor+4],'little');stop=cursor+length;req(stop<=len(payload) and payload[cursor+4:cursor+6]==b'\x01\0','property block framing');nested=int.from_bytes(payload[cursor+6:cursor+10],'little');name_length=int.from_bytes(payload[cursor+10:cursor+12],'little');req(nested==6+name_length,'property nested length');field=payload[cursor+12:cursor+12+name_length].decode('cp1252');rest=payload[cursor+12+name_length:stop];req(len(rest) in (9,18) and all(int.from_bytes(rest[i:i+2],'little')==9 for i in range(0,len(rest),9)),'property records');properties={};records=[]
  for i in range(0,len(rest),9):
   record=rest[i:i+9];index=int.from_bytes(record[4:6],'little');req(index<len(names),'property dictionary reference');req(record[:4]==bytes.fromhex('09000101') and record[6:8]==bytes.fromhex('0100') and record[-1] in (0,255),'Boolean property record');req(names[index] not in properties,'duplicate property');properties[names[index]]=bool(record[-1]);records.append(record.hex())
  blocks.append({'field':field,'properties':properties,'records_hex':records});cursor=stop
 req(cursor==len(payload),'property payload consumed');return {'dictionary':names,'blocks':blocks}
def find_properties(data,table_name,catalog,structure):
 definition,pages,decoded=catalog._discover_catalog(data);no=catalog._ordinal(definition,'Name');po=catalog._ordinal(definition,'LvProp');group=next(g for g in definition['long_value_maps'] if g['column']==po);owned=set(catalog._locator_pages(data,group['owned'],'LvProp owned'));hits=[]
 for row in decoded:
  if row['values'][no]!=table_name:continue
  image=catalog._page(data,row['page'],'catalog');entry=catalog._row_directory(image,row['page'])[row['row']];vals,_=structure.layout(image[entry['start']:entry['end']],definition['columns'])
  if vals[po] is None:return None
  reached=set();payload,desc=structure.payload(data,vals[po],owned,reached);hits.append((payload,desc,reached))
 req(len(hits)==1,table_name+' property row inventory');return hits[0]
def verify_maps(data,named,catalog,allocation,label):
 locations={'global':{'page':1,'row':0}};owners=[];available=[]
 for name,entry in named.items():
  table=entry['definition']
  for role,locator in table['maps'].items():locations[f'{name}/table/{role}']=locator
  owners.append(f'{name}/table/owned');available.append((f'{name}/table/available',f'{name}/table/owned'))
  for physical in table['physical_indexes']:
   key=f"{name}/index/{physical['index']}";locations[key]=physical['map'];owners.append(key)
  for group in table['long_value_maps']:
   for role in ('owned','available'):locations[f"{name}/lval/{group['column']}/{role}"]=group[role]
   key=f"{name}/lval/{group['column']}";owners.append(key+'/owned');available.append((key+'/available',key+'/owned'))
 req(len({(v['page'],v['row']) for v in locations.values()})==len(locations),label+' distinct map rows')
 records={};members={};bitmaps=set();owned=set()
 for role,locator in locations.items():
  records[role],members[role]=allocation.map_record(data,locator,role);refs={p for p in records[role]['references'] if p};req(not refs&bitmaps,label+' distinct bitmap pages');bitmaps|=refs
 for role in owners:req(not members[role]&(owned|members['global']),label+' ownership separation '+role);owned|=members[role]
 for avail,owner in available:req(members[avail]<=members[owner],label+' availability subset '+avail)
 metadata=bitmaps|{v['page'] for v in locations.values()}|{p for t in named.values() for p in t['definition']['pages']};req(not metadata&(owned|members['global']),label+' metadata separation')
 return records
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--matrix',type=Path,required=True);ap.add_argument('--run',type=Path,required=True);ap.add_argument('--producer',type=Path,required=True);ap.add_argument('--report',type=Path,required=True);ap.add_argument('--progress-logs',action='store_true');a=ap.parse_args();a.outbox=a.run/'outbox';req(not a.report.exists(),'report already exists')
 plan=json.loads(a.matrix.read_text());submitted=checks.run_inputs(a.run,a.matrix,a.producer,plan,a.progress_logs)
 import multiple_relationship_structure as raw
 import system_catalog as catalog
 import relationship_mutation_structure as structure
 import wide_row_lifecycle_structure as wide
 import allocation_lifecycle_structure as allocation
 expected={(g['name'],r) for g in plan['graphs'] for r in range(1,plan['replicas']+1)};found=set();observations=[];errors=[]
 for case in plan['graphs']:
  case=resolve_auto(case)
  for replica in range(1,plan['replicas']+1):
   stem=f"{case['name']}-r{replica}";found.add((case['name'],replica))
   try:
    receipt=json.loads((a.outbox/f'{stem}.json').read_text());req(receipt['status']=='pass' and receipt['error'] is None,stem+' worker');req(receipt['source_revision']==plan['source_revision'],stem+' source');req(receipt['matrix_sha256']==ident(a.matrix)['sha256'],stem+' matrix');req(receipt['environment']==checks.PROVIDER,stem+' provider')
    req(receipt['case']==stem and receipt['replica']==replica,stem+' receipt identity');req(receipt['candidate']['before']==submitted[stem],stem+' submitted candidate');model={t['name']:t['rows'] for t in case['tables']};roleobs={}
    for role in ('candidate','control'):
     path=a.outbox/f'{stem}-{role}.mdb';cap=receipt[role];req(cap['before']==cap['after']==ident(path),stem+'/'+role+' identity');roleobs[role]=raw.observe(path,cap,case,model,stem+'/'+role)
     checks.schema(cap['snapshot'],case,stem+'/'+role);checks.index_reads(cap['snapshot'],stem+'/'+role);checks.prefixes(roleobs[role],model,role,stem);data=path.read_bytes();analysis=catalog.analyze_checkpoint(data);named={t['name']:t for t in analysis['tables'].values()};dao={t['name']:t for t in cap['snapshot']['user_tables']}
     req(set(named)==set(checks.SYSTEM_INDEXES)|set(model) and len(named)==len(analysis['tables']),stem+'/'+role+' exact raw table inventory')
     property_report={};property_fragments=set()
     for table in case['tables']:
      prop=find_properties(data,table['name'],catalog,structure);want=expected_properties(table['fields']);req(prop is not None,stem+'/'+role+' properties '+table['name']);req(not property_fragments&prop[2],stem+'/'+role+' distinct property fragments');property_fragments.update(prop[2]);decoded=decode_properties(prop[0]);byfield={b['field']:b['properties'] for b in decoded['blocks']};checks.properties(decoded,table['fields'],stem+'/'+role)
      for field in table['fields']:
       if field.get('attributes')==16:continue
       req(byfield[field['name']]['Required'] is False,stem+'/'+role+' Required '+field['name'])
       if field['type'] in (10,12):req(byfield[field['name']]['AllowZeroLength']==field.get('allow_zero_length',True),stem+'/'+role+' AllowZeroLength '+field['name'])
      if role=='candidate' or not any(f.get('attributes')==16 for f in table['fields']):req(prop[0]==want,stem+'/'+role+' deterministic properties '+table['name'])
      property_report[table['name']]={'length':len(prop[0]),'sha256':hashlib.sha256(prop[0]).hexdigest(),'payload_hex':prop[0].hex(),'dictionary':decoded['dictionary'],'blocks':decoded['blocks'],'fragments':sorted(({'page':p,'row':r} for p,r in prop[2]),key=lambda x:(x['page'],x['row']))}
      d=named[table['name']]['definition'];pages=catalog._locator_pages(data,d['maps']['owned'],'table data');wide.table_rows(data,d,pages)
      req(len(flatten(dao[table['name']]['fields']))==len(table['fields']),stem+'/'+role+' field inventory')
     verify_maps(data,named,catalog,allocation,stem+'/'+role);roleobs[role]['storage']=checks.storage(path,analysis,roleobs[role])
     roleobs[role]['properties']=property_report
     if case['name'].startswith('boundary_catalog_continuations'):
      child=next(t for t in case['tables'] if t['name'].startswith('Boundary_Child'))['name'];d=named[child]['definition'];req(len(d['pages'])>=2,stem+'/'+role+' boundary definition continuation');req(len(d['long_value_maps'])==8,stem+'/'+role+' eight LVAL groups');prop=find_properties(data,child,catalog,structure);req(len(prop[2])>=2,stem+'/'+role+' chained properties');objpages={r['page'] for r in analysis['system_rows']['MSysObjects']['rows']};req(len(objpages)>=2,stem+'/'+role+' multi-page system catalog')
    cs=receipt['candidate']['snapshot'];ns=receipt['control']['snapshot'];req(cs['tables']==ns['tables'] and cs['queries']==ns['queries'] and cs['relations']==ns['relations'],stem+' DAO top schema')
    cd={x['name']:x for x in cs['user_tables']};nd={x['name']:x for x in ns['user_tables']};req(set(cd)==set(nd)=={t['name'] for t in case['tables']},stem+' DAO table names')
    for name in cd:
     for key in ('attributes','fields','indexes','rows','index_reads'):
      left=cd[name][key];right=nd[name][key]
      if key=='fields':
       left=[dict(x,properties=stable_properties(x['properties'])) for x in flatten(left)];right=[dict(x,properties=stable_properties(x['properties'])) for x in flatten(right)]
      elif key=='indexes':left=flatten(left);right=flatten(right)
      req(left==right,stem+' DAO '+name+'/'+key)
     req(stable_properties(cd[name]['properties'])==stable_properties(nd[name]['properties']),stem+' DAO table properties '+name)
    if case['name'].startswith('duplicate_fk_distinct_targets'):
     for role in ('candidate','control'):
      pairs=roleobs[role]['pairs'];req(len(pairs)==2,stem+'/'+role+' two shared aliases');req(pairs[0]['child']['physical_index']==pairs[1]['child']['physical_index'],stem+'/'+role+' shared physical FK')
    observations.append({'case':case['name'],'replica':replica,'environment':receipt['environment'],'candidate':roleobs['candidate'],'control':roleobs['control']})
   except Exception as e:errors.append({'case':case['name'],'replica':replica,'error':f'{type(e).__name__}: {e}'})
 receipts={p.stem for p in a.outbox.glob('*.json')};expected_receipts={f'{n}-r{r}' for n,r in expected};extra=sorted(receipts-expected_receipts);missing=sorted(expected_receipts-receipts)
 if extra:errors.append({'inventory':'extra receipts','values':extra})
 if missing:errors.append({'inventory':'missing receipts','values':missing})
 progress={p.name:ident(p) for p in a.outbox.glob('progress-*.txt')} if a.progress_logs else {}
 report={'document_type':'relationship_graph_creation_acceptance','source_revision':plan['source_revision'],'matrix':ident(a.matrix),'outbox':str(a.outbox),'status':'accepted' if not errors and len(observations)==len(expected) else 'rejected','producer':ident(a.producer),'progress_logs':progress,'run_inputs':{'matrix':ident(a.matrix),'input_zip':ident(a.run/'inbox'/'inputs.zip')},'expected_pairs':len(expected),'observed_pairs':len(observations),'errors':errors,'observations':observations};a.report.write_text(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n');print(json.dumps({'status':report['status'],'observed_pairs':len(observations),'errors':errors},indent=2));return 0 if report['status']=='accepted' else 1
if __name__=='__main__':raise SystemExit(main())
