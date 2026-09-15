#!/usr/bin/env python3
from __future__ import annotations
def pat(n,s):return bytes((i*37+s*13+11)%256 for i in range(n))
def decode(v,name,row,col):
 if not isinstance(v,dict):return v
 k=v['kind']
 if k=='null':return None
 if k=='empty':return ''
 if k=='repeat':return (bytes([v['byte']])*v['length']).hex()
 if k=='pattern':return pat(v['length'],v['seed']).hex()
 if k=='ascii':return (f'label-{row}' if name=='Label' else f'r{row:02d}-c{col:02d}').encode().hex()
 raise ValueError(k)
def initial(case):
 out=[]
 for n,source in enumerate(case['rows']):
  row=[]
  for c,f in enumerate(case['fields']):
   v=decode(source[f['name']],f['name'],n,c)
   if f['type']==4 and f['name']=='Id' and v is None:v=n+1
   if v=='' and f['type']==11:v=None # empty OLE normalized in retained native/candidate sources
   row.append(v)
  out.append(row)
 return out
def op(kind,table,**kw):return {'kind':kind,'table':table,**kw}
def recipe(case):
 rows=initial(case);plain=case['name']=='plain';ids=[100,101,102,103,104] if plain else [1,2,3,4,5]
 r900=rows[4].copy();r900[0]=900;r900[1]=1;r900[3 if not plain else 2]=(b'i'*33).hex();r900[4 if not plain else 3]=pat(2037,900).hex()
 r901=rows[1].copy();r901[0]=901;r901[1]=None;r901[3 if not plain else 2]=b'j'.hex();r901[4 if not plain else 3]=pat(33,901).hex()
 changed0=rows[0].copy();changed0[1]=2;changed0[3 if not plain else 2]=(b'z'*4096).hex();changed0[4 if not plain else 3]=pat(1,700).hex()
 changed900=r900.copy();changed900[1]=3;changed900[3 if not plain else 2]=(b'changed-payload-900-abcdefghijklm')[:33].hex();changed900[4 if not plain else 3]=pat(2037,902).hex()
 orphan=r901.copy();orphan[0]=999;orphan[1]=999
 stages=[{'name':'original','operations':[]},
  {'name':'inserted','operations':[op('insert','Parent',row=[20,b'twenty'.hex()]),op('insert','Child',row=r900),op('insert','Child',row=r901)]},
  {'name':'changed','operations':[op('replace','Child',id=ids[0],row=changed0),op('replace','Child',id=900,row=changed900)]},
  {'name':'ordered-deletes','operations':[op('delete','Child',id=ids[1]),op('delete','Child',id=ids[2]),op('delete','Parent',id=1)]}]
 refusals=[{'name':'orphan-insert','number':3201,'operation':op('insert','Child',row=orphan)},
  {'name':'orphan-update','number':3201,'operation':op('field','Child',id=ids[0],column=1,value=999)},
  {'name':'referenced-parent-delete','number':3200,'operation':op('delete','Parent',id=1)}]
 native=[op('field','Child',id=900,column=1,value=2),op('delete','Child',id=901),op('delete','Parent',id=20)]
 return {'document_type':'rich_relationship_lifecycle_recipe','arm':case['name'],'initial_rows':rows,'stages':stages,'refusals':refusals,'native':native}
