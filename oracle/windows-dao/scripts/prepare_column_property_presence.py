"""Controlled LvProp-presence inputs from EXP-0060/0266/0283 grammar."""
import argparse,hashlib,json,subprocess,sys,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--source-outbox',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
repo=args.repo;sys.path.insert(0,str(repo/'oracle/windows-dao/scripts'))
import column_property_checks as props
import required_column_discovery as discovery
import system_catalog as catalog
import relationship_system_indexes as systems
root=args.output;root.mkdir(exist_ok=False);inputs=root/'inputs';inputs.mkdir()
source=args.source_outbox
def ident(p):b=p.read_bytes();return dict(size=len(b),sha256=hashlib.sha256(b).hexdigest())
def u16(v):return v.to_bytes(2,'little')
def u32(v):return v.to_bytes(4,'little')
def payload(blocks):
 names=[name for name in ('Required','AllowZeroLength') if any(name in values for _,values in blocks)]
 body=b''.join(u16(len(name))+name.encode() for name in names)
 result=b'KKD\0'+u32(6+len(body))+u16(128)+body
 for field,values in blocks:
  records=b''.join(u16(9)+b'\1\1'+u16(names.index(name))+b'\1\0'+bytes([255 if value else 0]) for name,value in values.items())
  result+=u32(12+len(field)+len(records))+u16(1)+u32(6+len(field))+u16(len(field))+field.encode()+records
 return result

def repack(data,page_number,replacements):
 begin=page_number*2048;image=bytes(data[begin:begin+2048]);entries=catalog._row_directory(image,page_number)
 rows=[]
 for entry in entries:
  flags=(0x8000 if entry['hidden'] else 0)|(0x4000 if entry['overflow'] else 0)
  assert flags==0 or (flags==0xc000 and entry['start']==entry['end'])
  row=replacements.get(entry['row'],image[entry['start']:entry['end']])
  if row is None:assert entry['row']==len(entries)-1
  else:rows.append((row,flags))
 out=bytearray(image);out[8:10]=u16(len(rows));end=2048
 for slot,(row,flags) in enumerate(rows):
  end-=len(row);out[end:end+len(row)]=row;out[10+2*slot:12+2*slot]=u16(end|flags)
 assert end>=10+2*len(rows);out[2:4]=u16(end-10-2*len(rows));data[begin:begin+2048]=out



def set_map_bit(data,locator,page,present):
 raw=catalog._locator_row(data,locator,'property allocation')
 if raw[0]==0:
  base=int.from_bytes(raw[1:5],'little');bit=page-base;assert bit>=0 and 5+bit//8<len(raw)
  image=catalog._page(data,locator['page'],'map');entry=catalog._row_directory(image,locator['page'])[locator['row']]
  offset=locator['page']*2048+entry['start']+5+bit//8
 else:
  assert raw[0]==1;slot,bit=divmod(page,16352);reference=int.from_bytes(raw[1+4*slot:5+4*slot],'little');assert reference
  offset=reference*2048+4+bit//8
 mask=1<<(bit%8)
 if present:data[offset]|=mask
 else:data[offset]&=255^mask


def replace_catalog_value(row,columns,ordinal,value):
 fields=props.row_layout(row,columns);fields[ordinal]=(value is not None,value)
 fixed=1+max(c['fixed_offset']+c['size'] for c in columns if c['storage']=='fixed')
 result=bytearray(row[:fixed]);presence=bytearray(row[-((len(columns)+7)//8):]);bit=1<<(ordinal%8)
 if value is None:presence[ordinal//8]&=255^bit
 else:presence[ordinal//8]|=bit
 bounds=[fixed]
 variables=sorted((c for c in columns if c['storage']=='variable'),key=lambda c:c['variable_index'])
 for column in variables:
  result.extend(fields[column['ordinal']][1] or b'');bounds.append(len(result))
 assert len(result)+len(bounds)+1+len(presence)<256
 result.extend(reversed(bounds));result.append(len(variables));result.extend(presence)
 assert props.row_layout(result,columns)==fields
 return bytes(result)

cases=[];artifacts=[]
modes=[('absent',False,False,False,False,False,[]),('other-field-only',False,False,False,False,True,[('Id',{'Required':False})]),('required-false-only',True,False,False,False,False,[('Payload',{'Required':False})]),('required-true-only',True,True,False,False,False,[('Payload',{'Required':True})]),('zero-length-false-only',False,False,True,False,False,[('Payload',{'AllowZeroLength':False})]),('zero-length-true-only',False,False,True,True,False,[('Payload',{'AllowZeroLength':True})])]
for name,kind,size,attributes in [('Text',10,16,2),('FixedText',10,4,1),('Memo',12,0,2)]:
 for mode,set_req,required,set_alz,alz,other,blocks in modes:
  case=dict(id=name.lower()+'-'+mode,name=name,type=kind,size=size,attributes=attributes,required=required,allow_zero_length=alz,set_required=set_req,set_allow_zero_length=set_alz,other_field_property=other,inputs={})
  for replica in (1,2):
   before=source/f'r{replica}-{name.lower()}-required-0-zero-0-initial.mdb';original=before.read_bytes();data=bytearray(original);old,header=props.property_payload(original,'Rows');assert header['flags']==0x40000000 and len(header['chain'])==1
   definition,pages,_=catalog._discover_catalog(original);colname,lvprop=(catalog._ordinal(definition,n) for n in ('Name','LvProp'))
   new_payload=payload(blocks) if blocks else None
   found=[]
   for page in pages:
    image=catalog._page(original,page,'catalog')
    for entry in catalog._row_directory(image,page):
     if entry['hidden']:continue
     row=image[entry['start']:entry['end']];fields=props.row_layout(row,definition['columns'])
     if fields[colname][1]==b'Rows':
      old_header=fields[lvprop][1];assert old_header.hex()==header['raw_hex'];new_header=None if new_payload is None else u32(0x40000000|len(new_payload))+old_header[4:]
      repack(data,page,{entry['row']:replace_catalog_value(row,definition['columns'],lvprop,new_header)});found.append((page,entry['row']))
   assert len(found)==1
   page,slot=header['chain'][0];repack(data,page,{slot:new_payload})
   if new_payload is None:
    assert all(e['hidden'] and e['start']==e['end'] for e in catalog._row_directory(catalog._page(data,page,'released property'),page))
    group=next(g for g in definition['long_value_maps'] if g['column']==lvprop)
    for role in ('owned','available'):set_map_bit(data,group[role],page,False)
    set_map_bit(data,dict(page=1,row=0),page,True)
   out=inputs/f'r{replica}-{case["id"]}.mdb';out.write_bytes(data)
   assert systems.inventory(before)==systems.inventory(out)
   if new_payload is not None:assert props.property_payload(data,'Rows')[0]==new_payload
   run=subprocess.run([str(args.binary),'validate',str(out)],capture_output=True,text=True)
   (inputs/(out.stem+'.validate.stdout')).write_text(run.stdout);(inputs/(out.stem+'.validate.stderr')).write_text(run.stderr);assert run.returncode==0,(out.name,run.stderr)
   case['inputs'][str(replica)]=dict(file=out.name,identity=ident(out))
   artifacts.append(dict(file=out.name,source=str(before),source_identity=ident(before),identity=ident(out),changed_pages=sorted({i//2048 for i,(a,b) in enumerate(zip(original,data)) if a!=b}),property_payload_hex=None if new_payload is None else new_payload.hex(),property_model=None if new_payload is None else discovery.dictionary_and_blocks(new_payload)))
  cases.append(case)
matrix=dict(document_type='column_property_presence_inputs',replicas=2,cases=cases)
(root/'matrix.json').write_text(json.dumps(matrix,indent=2,sort_keys=True)+'\n')
with zipfile.ZipFile(root/'inputs.zip','x',compression=zipfile.ZIP_STORED) as z:
 for artifact in artifacts:z.write(inputs/artifact['file'],artifact['file'])
(root/'preparation.json').write_text(json.dumps(dict(status='prepared_not_native_verified',source_revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),matrix=ident(root/'matrix.json'),bundle=ident(root/'inputs.zip'),artifacts=artifacts),indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(status='prepared_not_native_verified',cases=18,inputs=36,checkpoints=288,bundle=ident(root/'inputs.zip'))))
