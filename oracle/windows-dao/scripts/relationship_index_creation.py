#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sys, zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
import relationship_index_checks as discovery
import relationship_system_indexes as system_index_inventory

PROVIDER={'ansi':1252,'bits':32,'culture':'en-US','dll_sha256':'4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac','dll_version':'03.60.9765.0','os':'Microsoft Windows NT 10.0.20348.0','provider':'DAO.DBEngine.36','version':'3.6'}
from relationship_mutation_structure import logical_relation

def req(ok,msg):
    if not ok:raise AssertionError(msg)
def sha(data):return hashlib.sha256(data).hexdigest()
def ident(path):
    data=path.read_bytes();return {'size':len(data),'sha256':sha(data)}
def pseudo(capture,graph,replica):
    return {'status':'pass','error':None,'table_error':None,'capture':capture,'replica':replica,
            'environment':{},'relations':[{'name':relation['name'],'error':None} for relation in graph['relations']]}
def table_name_by_root(raw):return {table['root']:name for name,table in raw['tables'].items()}
def logical_semantics(raw,table_name):
    table=raw['tables'][table_name];roots=table_name_by_root(raw);result=[]
    for item in table['logical_indexes']:
        if item['class']==2:
            record=logical_relation(item['raw_hex']);physical=table['physical_indexes'][record['physical_index']]
            result.append({'name':item['name'],'class':2,'selector':record['selector'],'side':record['side'],
                'relation_ordinal':record['relation_ordinal'],'context':record['context_hex'],'related_table':roots[record['related_root']],
                'selected_flags':physical['flags'],'selected_keys':physical['keys']})
        else:
            encoded=bytes.fromhex(item['raw_hex']);physical_index=int.from_bytes(encoded[4:8],'little');physical=table['physical_indexes'][physical_index]
            result.append({'name':item['name'],'class':item['class'],'selected_flags':physical['flags'],'selected_keys':physical['keys']})
    return result
def validate_counters(raw,graph,role):
    specs={table['name']:table for table in graph['tables']}
    for table_name,spec in specs.items():
        table=raw['tables'][table_name]
        for physical in table['physical_indexes']:
            entries=[bytes.fromhex(value) for value in physical['entries_hex']]
            req(physical['second_word']==len({entry[:-4] for entry in entries}),f'{graph["id"]}/{role}/{table_name}/{physical["index"]} distinct counter')
            if role=='candidate':req(physical['first_word']==0,f'{graph["id"]} candidate first word')
            else:
                # EXP-0286: rows-before-relation history seeds both generated endpoint trees.
                ordinal=physical['index']
                declared=any(item['class']!=2 and int.from_bytes(bytes.fromhex(item['raw_hex'])[4:8],'little')==ordinal for item in table['logical_indexes'])
                related=any(item['class']==2 and logical_relation(item['raw_hex'])['physical_index']==ordinal for item in table['logical_indexes'])
                expected=len(spec['rows']) if related and not declared else 0
                req(physical['first_word']==expected,f'{graph["id"]}/{table_name}/{ordinal} native selected prefix history')
def inbox_for(outbox):
    retained=outbox.parent/'inbox'
    return retained if retained.is_dir() else outbox.parent.parent/'inbox'/outbox.name
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence-root',type=Path,required=True);ap.add_argument('matrix',type=Path);ap.add_argument('bundle',type=Path);ap.add_argument('outbox',type=Path);ap.add_argument('report',type=Path);a=ap.parse_args()
    matrix=json.loads(a.matrix.read_text());graphs={graph['id']:graph for graph in matrix['graphs']};pairs=[(replica,graph['id']) for graph in matrix['graphs'] for replica in range(1,matrix['replicas']+1)]
    final_verification=json.loads((a.evidence_root/'final-source-verification.json').read_text())
    creation_checks=[item for item in final_verification['checks'] if item['reference'].startswith('creation-r1/')]
    req(final_verification['source_revision']=='2c4a77f02001beb78ebd8415392c91d08b3c8f1d' and len(creation_checks)==40 and all(item['byte_exact'] for item in creation_checks),'final source/creation byte equivalence')
    allowed={'workers.json','exit.txt','log.txt'}|{f'r{replica}-{case}{suffix}' for replica,case in pairs for suffix in ('-result.json','-candidate.mdb','-native.mdb')}
    actual={path.name for path in a.outbox.iterdir() if path.is_file()};req(actual==allowed,f'outbox inventory missing={sorted(allowed-actual)} extra={sorted(actual-allowed)}')
    req((a.outbox/'exit.txt').read_text().strip()=='0' and not (a.outbox/'log.txt').read_text(encoding='utf-8-sig').strip(),'wrapper exit/log')
    inbox=inbox_for(a.outbox)
    req({p.name for p in inbox.iterdir() if p.is_file()}=={'script.ps1','matrix.json','inputs.zip'},'exact inbox inventory')
    req((inbox/'script.ps1').read_bytes()==(HERE/'relationship_index_acceptance.ps1').read_bytes() and (inbox/'matrix.json').read_bytes()==a.matrix.read_bytes() and (inbox/'inputs.zip').read_bytes()==a.bundle.read_bytes(),'submitted input bytes')
    workers=json.loads((a.outbox/'workers.json').read_text(encoding='utf-8-sig'));req(workers['matrix']==ident(a.matrix) and workers['bundle']==ident(a.bundle) and len(workers['workers'])==len(pairs),'workers identity/inventory')
    preparation=json.loads((a.evidence_root/'preparation.json').read_text());prepared={item['name']:{'size':item['size'],'sha256':item['sha256']} for item in preparation['files']}
    observations=[]
    with zipfile.ZipFile(a.bundle) as archive:
        req(archive.read('matrix.json')==a.matrix.read_bytes(),'bundle matrix')
        names=[name for name in archive.namelist() if not name.endswith('/')]
        expected_names={'matrix.json'}|{f'candidates/{graphs[case]["name"]}-r{replica}.mdb' for replica,case in pairs}
        req(len(names)==len(set(names)) and set(names)==expected_names,'exact unique ZIP inventory')
        for position,(replica,case_id) in enumerate(pairs):
            graph=graphs[case_id];worker=workers['workers'][position];result_path=a.outbox/f'r{replica}-{case_id}-result.json'
            req((worker['replica'],worker['case'],worker['exit_code'],worker['result'])==(replica,case_id,0,ident(result_path)),'worker linkage')
            result=json.loads(result_path.read_text(encoding='utf-8-sig'));req(result['status']=='pass' and result['error'] is None and result['case']==case_id and result['replica']==replica and result['matrix']==ident(a.matrix) and result['bundle']==ident(a.bundle),'result identity/status');req(result['environment']==PROVIDER,'exact provider identity')
            entry=f'candidates/{graph["name"]}-r{replica}.mdb';source={'size':len(archive.read(entry)),'sha256':sha(archive.read(entry))};req(source==prepared[Path(entry).name]==result['candidate']['source'],'candidate source pin')
            candidate_path=a.outbox/result['candidate']['file'];native_path=a.outbox/result['native']['file']
            req(result['candidate']['capture']['before']==result['candidate']['capture']['after']==ident(candidate_path)==source,'candidate read-only/copy identity')
            req(result['native']['creation_error'] is None and all(item['error'] is None for item in result['native']['relations']),'native creation')
            req(result['native']['capture']['before']==result['native']['capture']['after']==ident(native_path),'native read-only/copy identity')
            candidate=discovery.evaluate_case(graph,pseudo(result['candidate']['capture'],graph,replica),candidate_path)
            native=discovery.evaluate_case(graph,pseudo(result['native']['capture'],graph,replica),native_path)
            candidate_system_indexes=system_index_inventory.inventory(candidate_path)
            native_system_indexes=system_index_inventory.inventory(native_path)
            req(discovery.normalized_snapshot(candidate['snapshot'])==discovery.normalized_snapshot(native['snapshot']),f'{case_id}/r{replica} DAO candidate/native snapshot')
            for table in graph['tables']:
                name=table['name'];req(candidate['raw']['tables'][name]['columns']==native['raw']['tables'][name]['columns'],f'{case_id}/{name} raw columns')
                req(logical_semantics(candidate['raw'],name)==logical_semantics(native['raw'],name),f'{case_id}/{name} logical index semantics')
                req([row['values'] for row in candidate['raw']['tables'][name]['rows']]==[row['values'] for row in native['raw']['tables'][name]['rows']],f'{case_id}/{name} raw values')
            for key in ('relationship_rows','relationship_objects','relationship_aces'):
                req(candidate['raw'][key]==native['raw'][key],f'{case_id} {key}')
            validate_counters(candidate['raw'],graph,'candidate');validate_counters(native['raw'],graph,'native')
            observations.append({'case':case_id,'replica':replica,'environment':result['environment'],'candidate_source':source,'candidate':candidate,'native':native,'candidate_system_indexes':candidate_system_indexes,'native_system_indexes':native_system_indexes})
    for case_id in graphs:
        pair=[item for item in observations if item['case']==case_id];req(len(pair)==2,'replica count')
        req([discovery.normalized_snapshot(item['candidate']['snapshot']) for item in pair][0]==[discovery.normalized_snapshot(item['candidate']['snapshot']) for item in pair][1],'candidate replica snapshot')
        req([discovery.normalized_snapshot(item['native']['snapshot']) for item in pair][0]==[discovery.normalized_snapshot(item['native']['snapshot']) for item in pair][1],'native replica snapshot')
    report={'document_type':'relationship_index_creation_acceptance_report','outcome':'accepted','source_revision':final_verification['source_revision'],'source_archive_sha256':final_verification['source_archive_sha256'],'candidate_binary_sha256':final_verification['candidate_binary_sha256'],'final_source_verification':ident(a.evidence_root/'final-source-verification.json'),'provider':PROVIDER,'matrix':ident(a.matrix),'bundle':ident(a.bundle),'producer':ident(HERE/'relationship_index_acceptance.ps1'),'evaluator':ident(Path(__file__)),'pairs':len(observations),'captures':2*len(observations),'observations':observations}
    a.report.write_text(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n');print(a.report);print('accepted',len(observations),'pairs')
if __name__=='__main__':main()
