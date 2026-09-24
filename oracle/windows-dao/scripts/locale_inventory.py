#!/usr/bin/env python3
"""Extract complete native keys and validate row/index inventories for EXP-0309."""
import argparse,json,sys,hashlib
from pathlib import Path
scripts=Path(__file__).resolve().parent
import schema_edit_structure as raw
raw.setup(scripts)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--captures',type=Path,required=True)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()

def entries(data,root,owner):
 visited=set()
 def walk(n):
  assert 0<n<len(data)//2048 and n not in visited
  visited.add(n);p=data[n*2048:(n+1)*2048];assert p[0] in (3,4) and int.from_bytes(p[4:8],'little')==owner
  prefix=p[20];area=p[248:];ends=[i*8+b for i,v in enumerate(p[22:248]) for b in range(8) if v&(1<<b)]
  start=0;result=[]
  for end in ends:
   assert start<end<=len(area)
   entry=(area[:prefix] if start else b'')+area[start:end]
   assert len(entry)>=4
   if p[0]==4:result.append(entry)
   else:result.extend(walk(int.from_bytes(entry[-4:],'big')))
   start=end
  if p[0]==3:result.extend(walk(int.from_bytes(p[16:20],'little')))
  assert result==sorted(result)
  return result
 return walk(root)

all_results={}
for name in ['general','nordic','spanish','dutch','cyrillic','greek']:
 path=args.captures/(name+'.mdb');data=path.read_bytes();dao=json.loads((args.captures/(name+'.json')).read_text(encoding='utf-8-sig'))
 assert dao['error'] is None and hashlib.sha256(data).hexdigest()==dao['identity']['sha256']==dao['closed_identity']['sha256']
 a=raw.catalog.analyze_checkpoint(data);t,=[t for t in a['tables'].values() if t['name']=='T'];d=t['definition'];cols=[dict(c,variable_high_water=d['variable_high_water']) for c in d['columns']]
 rows={};locs={}
 for n in t['data_pages']:
  p=data[n*2048:(n+1)*2048]
  for e in raw.directory(p,n):
   assert not e['hidden'] and not e['overflow']
   values=raw.fields(p[e['start']:e['end']],cols);i=int.from_bytes(values[0],'little',signed=True)
   assert i not in rows
   rows[i]=values[1];locs[n.to_bytes(3,'big')+bytes([e['row']])]=i
 assert len(rows)==d['row_count']==len(dao['rows'])
 for r in dao['rows']:
  # DAO 3.6 BSTR conversion uses the provider host ANSI page (1252).
  assert rows[r['id']]==r['value'].encode('cp1252',errors='strict') if all(ord(c) not in (0x81,0x8d,0x8f,0x90,0x9d) for c in r['value']) else rows[r['id']]==bytes(ord(c) if ord(c)<256 else c.encode('cp1252')[0] for c in r['value'])
 maps={}
 for index in d['logical_indexes']:
  es=entries(data,d['physical_indexes'][index['physical_index']]['root'],d['root'])
  ids=[locs[e[-4:]] for e in es]
  assert sorted(ids)==sorted(rows)
  order,=[x['ids'] for x in dao['indexes'] if x['name']==index['name']]
  assert ids==order
  maps[index['name']]={locs[e[-4:]]:e[:-4].hex() for e in es}
 keys={rows[i].hex():key for i,key in maps['ix'].items()}
 assert len(keys)==len(rows)
 for i,key in maps['ix'].items():
  if len(bytes.fromhex(key)) < 255: assert bytes(v^255 for v in bytes.fromhex(key)).hex()==maps['dx'][i]
 result={'source':dao['identity'],'header':data[0x3a:0x3e].hex(),'columns':d['columns'],'keys':keys}
 all_results[name]=result
 print(name,'rows',len(rows),flush=True)
args.out.write_text(json.dumps(all_results,indent=2)+'\n')
