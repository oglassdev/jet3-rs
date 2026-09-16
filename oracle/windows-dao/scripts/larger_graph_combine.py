import argparse,hashlib,json,shutil,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('inputs',nargs='+',type=Path);a=p.parse_args()
def ident(path):
 b=path.read_bytes();return {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()}
a.output.mkdir(exist_ok=False);groups=[];seen=set();sources=[];revision=None;binary=None
for source in a.inputs:
 matrix=json.loads((source/'matrix.json').read_text())
 if revision is None:revision=matrix['source_revision'];binary=matrix['binary']
 assert matrix['source_revision']==revision and matrix['binary']==binary
 sources.append({'path':str(source),'matrix':ident(source/'matrix.json'),'bundle':ident(source/'inputs.zip')})
 for group in matrix['groups']:
  assert group['id'] not in seen;seen.add(group['id']);groups.append(group)
  shutil.copytree(source/group['id'],a.output/group['id'])
result={'document_type':'larger_relationship_graph_lifecycle_preparation','source_revision':revision,'binary':binary,'groups':groups,'sources':sources,'status':'accepted'}
(a.output/'matrix.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
with zipfile.ZipFile(a.output/'inputs.zip','x',compression=zipfile.ZIP_STORED) as z:
 z.write(a.output/'matrix.json','matrix.json')
 for group in groups:
  z.write(a.output/group['initial'],group['initial'])
  for event in group['events']:z.write(a.output/event['stage'],event['stage'])
print(json.dumps({'groups':len(groups),'stages':sum(len(g['events']) for g in groups),'refusals':sum(e['exit']!=0 for g in groups for e in g['events']),'bundle':ident(a.output/'inputs.zip')}))
