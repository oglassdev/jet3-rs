#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path


def text_name(i:int)->str:
    prefix=f'Text_Property_Column_{i:02d}_'
    return prefix + ('X'*(48-len(prefix)))

def val(kind:str,length:int=0,seed:int=0,byte:int=65):
    return {'kind':kind,'length':length,'seed':seed,'byte':byte}

def fields(arm:str):
    result=[
      {'name':'Id','type':4,'size':4,'attributes':16 if arm!='plain' else 0,'allow_zero_length':False},
      {'name':'ParentId','type':4,'size':4,'attributes':0,'allow_zero_length':False},
    ]
    if arm!='plain': result.append({'name':'Label','type':10,'size':32,'attributes':0,'allow_zero_length':True})
    result += [
      {'name':'Body','type':12,'size':0,'attributes':0,'allow_zero_length':arm!='plain'},
      {'name':'Blob','type':11,'size':0,'attributes':0,'allow_zero_length':False},
    ]
    if arm=='boundary':
      for i in range(1,7):
        memo=i%2==1
        result.append({'name':f'Extra {i:02d} '+('Memo' if memo else 'OLE'),'type':12 if memo else 11,'size':0,'attributes':0,'allow_zero_length':memo})
      for i in range(1,33): result.append({'name':text_name(i),'type':10,'size':64,'attributes':0,'allow_zero_length':True})
    return result

def rows(arm:str):
    ids=[100,101,102,103,104] if arm=='plain' else [None]*5
    fks=[None,1,1,2,3]
    body=[val('null'),val('empty') if arm!='plain' else val('repeat',1,byte=66),val('repeat',33,byte=67),val('repeat',2037,byte=68),val('repeat',4096,byte=69)]
    blob=[val('empty'),val('pattern',1,11),val('pattern',2036,12),val('pattern',33,13),val('pattern',4096,14)]
    out=[]
    for n in range(5):
      values={'Id':ids[n],'ParentId':fks[n]}
      if arm!='plain': values['Label']=val('empty') if n==0 else val('ascii',length=len(f'label-{n}'),byte=0)
      values['Body']=body[n];values['Blob']=blob[n]
      if arm=='boundary':
        lengths=[None,0,1,33,2036,4096]
        for i in range(1,7):
          memo=i%2==1; length=lengths[(n+i-1)%len(lengths)]
          recipe=val('null') if length is None else val('empty') if length==0 else val('repeat',length,byte=70+n+i) if memo else val('pattern',length,20+n+i)
          values[f'Extra {i:02d} '+('Memo' if memo else 'OLE')]=recipe
        for i in range(1,33):
          name=text_name(i); values[name]=val('empty') if (n+i)%5==0 else val('ascii',length=len(f'r{n:02d}-c{i:02d}'),byte=0)
      out.append(values)
    return out

parser=argparse.ArgumentParser(description='Generate the three replicated rich relationship creation cases.')
parser.add_argument('output',type=Path)
parser.add_argument('revision')
args=parser.parse_args()
OUT=args.output
OUT.mkdir()
source=args.revision
arms=[]
for arm in ('plain','rich','boundary'):
  arms.append({'name':arm,'fields':fields(arm),'rows':rows(arm),'parents':[{'Id':1,'Label':'one'},{'Id':2,'Label':'two'},{'Id':3,'Label':'three'}],
               'indexes':[{'table':'Parent','name':'ByParent','field':'Id','primary':True}, {'table':'Child','name':'ByChild','field':'Id','primary':True}],
               'relation':{'name':'ParentChild','table':'Parent','foreign_table':'Child','field':'Id','foreign_field':'ParentId','attributes':0}})
doc={'document_type':'rich_relationship_creation_recipe','source_revision':source,'database':';LANGID=0x0409;CP=1252;COUNTRY=0','replicas':2,'arms':arms,
     'payload_rule':{'pattern':'byte[i]=(i*37+seed*13+11)%256','repeat':'byte repeated length times','ascii':'literal label-N, rNN-cNN recipes implied by row and column indices'},
     'notes':'Relation appended after both tables, both primary indexes, and all rows. Empty OLE assignment is expected to normalize to Null.'}
raw=(json.dumps(doc,sort_keys=True,separators=(',',':'))+'\n').encode()
(OUT/'matrix.json').write_bytes(raw)
(OUT/'matrix.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  matrix.json\n')
print(hashlib.sha256(raw).hexdigest())
